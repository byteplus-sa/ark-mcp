"""Timeout semantics, variation deadlines, download retries, and persistence fallbacks."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from ark_mcp.artifacts.store import ArtifactPersistenceError
from ark_mcp.config.env import get_settings
from ark_mcp.domain.artifacts import ArtifactRef, MediaType
from ark_mcp.domain.errors import NormalizedProviderError, ProviderError
from ark_mcp.domain.models import VariationResult
from ark_mcp.providers.modelark.client import ModelArkGateway
from ark_mcp.providers.retry import RetryPolicy, call_with_retry
from ark_mcp.providers.vod_mediakit.client import VodMediaKitGateway
from ark_mcp.runtime import billed_provider_slot, close_runtime_services, create_runtime_services
from ark_mcp.security.safe_downloader import SafeDownloader, SafeDownloadError
from ark_mcp.tools._parallel import VariationProgress, run_variation_batch
from ark_mcp.tools._persistence import (
    INLINE_FALLBACK_ARTIFACT_ID,
    PROVIDER_URL_ARTIFACT_ID,
    persist_base64,
    persist_from_url,
)
from tests.fixtures.fake_context import FakeContext


def _provider_error(code: str, *, retryable: bool = True, ambiguous: bool = False) -> ProviderError:
    return ProviderError(
        NormalizedProviderError(
            provider="modelark",
            operation="chat_completion",
            code=code,
            message="boom",
            retryable=retryable,
            ambiguous_completion=ambiguous,
        )
    )


# --- 1.1 timeout normalization -------------------------------------------------


def test_mutation_timeout_is_ambiguous_and_not_retryable() -> None:
    exc = ModelArkGateway.normalize_timeout("create_task")
    assert exc.code == "TIMEOUT"
    assert exc.retryable is False
    assert exc.ambiguous_completion is True


def test_read_only_timeout_is_retryable_and_not_ambiguous() -> None:
    exc = ModelArkGateway.normalize_timeout("get_task", side_effect=False)
    assert exc.retryable is True
    assert exc.ambiguous_completion is False
    assert "safe to retry" in exc.message


def test_vod_poll_transport_error_is_retryable() -> None:
    poll = VodMediaKitGateway.normalize_ambiguous_transport_error(
        "get_task", code="TIMEOUT", message="poll timed out", side_effect=False
    )
    submit = VodMediaKitGateway.normalize_ambiguous_transport_error(
        "submit", code="TIMEOUT", message="submit timed out"
    )
    assert (poll.retryable, poll.ambiguous_completion) == (True, False)
    assert (submit.retryable, submit.ambiguous_completion) == (False, True)


async def test_retry_timeouts_false_reraises_timeout_immediately() -> None:
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        raise _provider_error("TIMEOUT")

    async def sleep(_delay: float) -> None:
        return None

    with pytest.raises(ProviderError):
        await call_with_retry(operation, policy=RetryPolicy(retry_timeouts=False), sleep=sleep)
    assert attempts == 1


async def test_retry_timeouts_false_still_retries_rate_limits() -> None:
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            raise _provider_error("RATE_LIMITED")
        return "ok"

    async def sleep(_delay: float) -> None:
        return None

    result = await call_with_retry(operation, policy=RetryPolicy(retry_timeouts=False), sleep=sleep)
    assert result == "ok"
    assert attempts == 2


async def test_read_only_timeouts_are_auto_retried() -> None:
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ModelArkGateway.normalize_timeout("get_task", side_effect=False)
        return "ok"

    async def sleep(_delay: float) -> None:
        return None

    assert await call_with_retry(operation, sleep=sleep) == "ok"
    assert attempts == 3


# --- 1.1 budget ledger ---------------------------------------------------------


class _RecordingLedger:
    def __init__(self) -> None:
        self.events: list[str] = []

    async def reserve(self, _owner: Any, _estimate: Any) -> str:
        self.events.append("reserve")
        return "reservation"

    async def commit(self, _reservation: Any) -> None:
        self.events.append("commit")

    async def release(self, _reservation: Any) -> None:
        self.events.append("release")


@pytest.fixture
async def ledger_ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
    monkeypatch.setenv("ARTIFACT_DIR", str(tmp_path / ".artifacts"))
    monkeypatch.setenv("ARTIFACT_BACKEND", "filesystem")
    get_settings.cache_clear()
    runtime = await create_runtime_services(get_settings())
    real_ledger = runtime.budget_ledger
    ledger = _RecordingLedger()
    runtime.budget_ledger = ledger  # type: ignore[assignment]
    try:
        yield FakeContext(lifespan_context={"runtime": runtime}), ledger
    finally:
        runtime.budget_ledger = real_ledger
        await close_runtime_services(runtime)
        get_settings.cache_clear()


async def test_timeout_commits_reservation(ledger_ctx: Any) -> None:
    ctx, ledger = ledger_ctx
    with pytest.raises(ProviderError):
        async with billed_provider_slot(
            ctx, provider="modelark", product="understanding", estimated_cost_usd=0.01
        ):
            raise ModelArkGateway.normalize_timeout("chat_completion", side_effect=False)
    assert ledger.events == ["reserve", "commit"]


async def test_non_timeout_error_releases_reservation(ledger_ctx: Any) -> None:
    ctx, ledger = ledger_ctx
    with pytest.raises(ProviderError):
        async with billed_provider_slot(
            ctx, provider="modelark", product="image", estimated_cost_usd=0.01
        ):
            raise _provider_error("BAD_REQUEST", retryable=False)
    assert ledger.events == ["reserve", "release"]


async def test_cancellation_after_dispatch_commits_reservation(ledger_ctx: Any) -> None:
    ctx, ledger = ledger_ctx
    started = asyncio.Event()

    async def call() -> None:
        async with billed_provider_slot(
            ctx, provider="modelark", product="image", estimated_cost_usd=0.01
        ):
            started.set()
            await asyncio.sleep(60)

    task = asyncio.create_task(call())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ledger.events == ["reserve", "commit"]


async def test_cancellation_while_waiting_for_slot_releases_reservation(
    ledger_ctx: Any,
) -> None:
    ctx, ledger = ledger_ctx
    runtime = ctx.lifespan_context["runtime"]
    semaphore = runtime.provider_limiters.provider("modelark")
    held = [await semaphore.acquire() for _ in range(semaphore._value)]

    async def call() -> None:
        async with billed_provider_slot(
            ctx, provider="modelark", product="image", estimated_cost_usd=0.01
        ):
            pytest.fail("slot should never be acquired")

    task = asyncio.create_task(call())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in held:
        semaphore.release()
    assert ledger.events == ["reserve", "release"]


# --- 1.2 variation batch -------------------------------------------------------


async def test_queue_wait_does_not_count_against_variations() -> None:
    """With one slot, three sequential 0.3s variations must all succeed."""

    async def factory(idx: int, progress: VariationProgress) -> VariationResult:
        progress.phase = "generating"
        await asyncio.sleep(0.3)
        return VariationResult(index=idx, task_id=f"task-{idx}")

    summary = await run_variation_batch(3, factory, batch_deadline=5.0, max_concurrent=1)
    assert summary.succeeded == 3


async def test_batch_deadline_reports_phase() -> None:
    async def factory(idx: int, progress: VariationProgress) -> VariationResult:
        if idx == 0:
            progress.phase = "generating"
            await asyncio.sleep(10)
        return VariationResult(index=idx, task_id=f"task-{idx}")

    summary = await run_variation_batch(2, factory, batch_deadline=0.2, max_concurrent=1)
    first, second = summary.variations
    assert first.error is not None
    assert (first.error.code, first.error.phase, first.error.ambiguous_completion) == (
        "TIMEOUT",
        "generating",
        True,
    )
    assert second.error is not None
    assert (second.error.code, second.error.phase, second.error.retryable) == (
        "QUEUE_TIMEOUT",
        "queued",
        True,
    )


async def test_batch_deadline_while_persisting_returns_partial() -> None:
    partial_ref = ArtifactRef(
        id=PROVIDER_URL_ARTIFACT_ID,
        uri="https://media.byteplus.com/x.jpg",
        media_type=MediaType.IMAGE,
        mime_type="image/jpeg",
        created_at="2026-09-22T00:00:00+00:00",
    )

    async def factory(idx: int, progress: VariationProgress) -> VariationResult:
        progress.partial = VariationResult(index=idx, artifact=partial_ref)
        progress.phase = "persisting"
        await asyncio.sleep(10)
        return VariationResult(index=idx)

    summary = await run_variation_batch(1, factory, batch_deadline=0.1)
    assert summary.variations[0].artifact == partial_ref
    assert summary.succeeded == 1


# --- 1.3 downloader retries ----------------------------------------------------


def _public_resolver(_hostname: str, _port: int) -> tuple[str, ...]:
    return ("93.184.216.34",)


def _trusted(hostname: str) -> bool:
    return hostname == "media.byteplus.com"


async def _no_sleep(_delay: float) -> None:
    return None


async def test_downloader_retries_timeouts_then_succeeds() -> None:
    attempts = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ReadTimeout("slow")
        return httpx.Response(200, content=b"ok", headers={"content-type": "image/png"})

    downloader = SafeDownloader(
        resolver=_public_resolver, transport=httpx.MockTransport(handler), sleep=_no_sleep
    )
    try:
        result = await downloader.download(
            "https://media.byteplus.com/a.png", trusted_hosts=_trusted, max_bytes=1024
        )
    finally:
        await downloader.close()
    assert result.body == b"ok"
    assert attempts == 3


@pytest.mark.parametrize("status", [404, 410])
async def test_downloader_does_not_retry_expired_sources(status: int) -> None:
    attempts = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status)

    downloader = SafeDownloader(
        resolver=_public_resolver, transport=httpx.MockTransport(handler), sleep=_no_sleep
    )
    try:
        with pytest.raises(SafeDownloadError) as exc_info:
            await downloader.download(
                "https://media.byteplus.com/a.png", trusted_hosts=_trusted, max_bytes=1024
            )
    finally:
        await downloader.close()
    assert exc_info.value.code == "source_expired"
    assert attempts == 1


async def test_downloader_does_not_retry_untrusted_host() -> None:
    downloader = SafeDownloader(resolver=_public_resolver, sleep=_no_sleep)
    try:
        with pytest.raises(SafeDownloadError) as exc_info:
            await downloader.download(
                "https://evil.example.com/a.png", trusted_hosts=_trusted, max_bytes=1024
            )
    finally:
        await downloader.close()
    assert exc_info.value.code == "untrusted_host"


async def test_downloader_gives_up_after_max_attempts() -> None:
    attempts = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503)

    downloader = SafeDownloader(
        resolver=_public_resolver,
        transport=httpx.MockTransport(handler),
        sleep=_no_sleep,
        max_attempts=2,
    )
    try:
        with pytest.raises(SafeDownloadError):
            await downloader.download(
                "https://media.byteplus.com/a.png", trusted_hosts=_trusted, max_bytes=1024
            )
    finally:
        await downloader.close()
    assert attempts == 2


# --- 1.3 persistence fallbacks -------------------------------------------------


class _FailingStore:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.put_calls = 0

    async def copy_from_trusted_url(self, **_kwargs: Any) -> ArtifactRef:
        raise self.exc

    async def put_base64(self, **_kwargs: Any) -> ArtifactRef:
        self.put_calls += 1
        raise self.exc


async def test_url_persistence_failure_returns_provider_url() -> None:
    store = _FailingStore(
        ArtifactPersistenceError("download_failed", "Download failed.", retryable=True)
    )
    ref = await persist_from_url(
        store,  # type: ignore[arg-type]
        url="https://media.byteplus.com/a.jpg",
        media_type=MediaType.IMAGE,
        mime_type="image/jpeg",
        source_expires_at="2026-09-23T00:00:00+00:00",
        auth=None,
    )
    assert ref.id == PROVIDER_URL_ARTIFACT_ID
    assert ref.uri == "https://media.byteplus.com/a.jpg"
    assert ref.persistence_error is not None
    assert ref.persistence_error.code == "download_failed"
    assert ref.persistence_error.source_url_expires_at == "2026-09-23T00:00:00+00:00"
    assert "https://" not in ref.persistence_error.message


async def test_base64_store_failure_returns_inline_fallback() -> None:
    data = base64.b64encode(b"RIFF-audio-bytes").decode()
    store = _FailingStore(ArtifactPersistenceError("storage_failed", "Disk full.", retryable=True))
    ref = await persist_base64(
        store,  # type: ignore[arg-type]
        data=data,
        media_type=MediaType.AUDIO,
        mime_type="audio/wav",
        source_expires_at=None,
        auth=None,
    )
    assert store.put_calls == 2  # retried once
    assert ref.id == INLINE_FALLBACK_ARTIFACT_ID
    assert ref.fallback_data == data
    assert ref.persistence_error is not None


async def test_base64_store_failure_above_cap_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARTIFACT_INLINE_FALLBACK_MAX_BYTES", "4")
    get_settings.cache_clear()
    store = _FailingStore(ArtifactPersistenceError("storage_failed", "Disk.", retryable=True))
    try:
        with pytest.raises(ArtifactPersistenceError):
            await persist_base64(
                store,  # type: ignore[arg-type]
                data=base64.b64encode(b"more than four bytes").decode(),
                media_type=MediaType.AUDIO,
                mime_type="audio/wav",
                source_expires_at=None,
                auth=None,
            )
    finally:
        get_settings.cache_clear()


async def test_base64_failure_prefers_provider_url() -> None:
    store = _FailingStore(ArtifactPersistenceError("storage_failed", "Disk.", retryable=False))
    ref = await persist_base64(
        store,  # type: ignore[arg-type]
        data=base64.b64encode(b"img").decode(),
        media_type=MediaType.IMAGE,
        mime_type="image/png",
        source_expires_at=None,
        auth=None,
        provider_url="https://media.byteplus.com/b.png",
    )
    assert store.put_calls == 1  # non-retryable: no second attempt
    assert ref.id == PROVIDER_URL_ARTIFACT_ID
    assert ref.fallback_data is None


async def test_result_returned_when_task_finishes_as_deadline_fires() -> None:
    """A variation cancelled at the deadline that still returned must not be discarded."""
    started = asyncio.Event()

    async def factory(idx: int, progress: VariationProgress) -> VariationResult:
        progress.phase = "generating"
        started.set()
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            # Completed just as the deadline fired: return normally.
            return VariationResult(index=idx, task_id=f"task-{idx}")
        return VariationResult(index=idx, task_id=f"task-{idx}")

    summary = await run_variation_batch(1, factory, batch_deadline=0.1)
    assert summary.variations[0].task_id == "task-0"
    assert summary.variations[0].error is None
    assert summary.succeeded == 1
