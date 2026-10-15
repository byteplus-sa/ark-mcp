"""Integration tests for the OmniHuman 2.0 MCP tools."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastmcp.tools import ToolResult

from ark_mcp.artifacts.store import ArtifactPersistenceError, PresignedArtifact
from ark_mcp.domain.artifacts import ArtifactRef, MediaType
from ark_mcp.domain.errors import NormalizedProviderError, ProviderError
from ark_mcp.providers.vision_cv.omnihuman import OmniHumanService
from ark_mcp.providers.vision_cv.schemas import (
    OmniHumanCancelResult,
    OmniHumanSubmission,
    OmniHumanTaskResult,
)
from ark_mcp.security.auth_context import AuthContext
from ark_mcp.tools.omnihuman_cancel_task import (
    OmniHumanCancelTaskInput,
    OmniHumanCancelTaskOutput,
    omnihuman_cancel_task,
)
from ark_mcp.tools.omnihuman_create_task import (
    OmniHumanCreateTaskInput,
    OmniHumanCreateTaskOutput,
    omnihuman_create_task,
)
from ark_mcp.tools.omnihuman_get_task import (
    OmniHumanGetTaskInput,
    OmniHumanTaskOutput,
    omnihuman_get_task,
)
from tests.fixtures.fake_context import FakeContext

IMAGE_URL = "https://media.example.com/face.png"
AUDIO_URL = "https://media.example.com/voice.mp3"
VIDEO_URL = "https://out.example.com/v.mp4"
ARTIFACT_ID = "11111111-1111-4111-8111-111111111111"


@pytest.fixture(autouse=True)
def omnihuman_env(test_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from ark_mcp.config.env import get_settings

    monkeypatch.setenv("BYTEPLUS_OMNIHUMAN_ENABLED", "true")
    monkeypatch.setenv("BYTEPLUS_CV_API_KEY", "test-cv-key")
    get_settings.cache_clear()


async def _close(_self: OmniHumanService) -> None:
    return None


@pytest.fixture
def submitted(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    captured: list[object] = []

    async def create(_self: OmniHumanService, request: object) -> OmniHumanSubmission:
        captured.append(request)
        return OmniHumanSubmission(task_id="oh-1", request_id="req-1")

    monkeypatch.setattr(OmniHumanService, "create", create)
    monkeypatch.setattr(OmniHumanService, "close", _close)
    return captured


def provider_error(*, retryable: bool = False, code: str = "50510") -> ProviderError:
    return ProviderError(
        NormalizedProviderError(
            provider="byteplus-vision-cv",
            operation="omnihuman_create_task",
            http_status=500,
            code=code,
            message="Algovproxy Risk Error",
            request_id="req-9",
            retryable=retryable,
            ambiguous_completion=False,
        )
    )


def stub_get(monkeypatch: pytest.MonkeyPatch, result: OmniHumanTaskResult | ProviderError) -> None:
    async def get(_self: OmniHumanService, _task_id: str) -> OmniHumanTaskResult:
        if isinstance(result, ProviderError):
            raise result
        return result

    monkeypatch.setattr(OmniHumanService, "get", get)
    monkeypatch.setattr(OmniHumanService, "close", _close)


def succeeded_result() -> OmniHumanTaskResult:
    return OmniHumanTaskResult(
        status="succeeded",
        request_id="req-2",
        video_url=VIDEO_URL,
        video_url_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


async def record_owner(fake_ctx: FakeContext, task_id: str = "oh-1") -> None:
    runtime = fake_ctx.lifespan_context["runtime"]
    await runtime.ownership_store.record("vision-cv", task_id, AuthContext())


class TestCreate:
    async def test_accepts_and_records_ownership(
        self, fake_ctx: FakeContext, submitted: list[object]
    ) -> None:
        result = await omnihuman_create_task(
            OmniHumanCreateTaskInput(image=IMAGE_URL, audio=AUDIO_URL, prompt="waves"), fake_ctx
        )

        assert isinstance(result, OmniHumanCreateTaskOutput)
        assert result.status == "accepted"
        assert result.task_id == "oh-1"
        assert result.request_id == "req-1"
        assert result.recommended_poll_after_ms > 0
        request = submitted[0].model_dump(mode="json")  # type: ignore[attr-defined]
        assert request["image_url"] == IMAGE_URL
        assert request["audio_url"] == AUDIO_URL
        assert request["prompt"] == "waves"
        assert request["enable_watermark"] is True
        runtime = fake_ctx.lifespan_context["runtime"]
        assert await runtime.ownership_store.list_task_ids("vision-cv", AuthContext()) == {"oh-1"}

    async def test_watermark_can_be_disabled(
        self, fake_ctx: FakeContext, submitted: list[object]
    ) -> None:
        await omnihuman_create_task(
            OmniHumanCreateTaskInput(image=IMAGE_URL, audio=AUDIO_URL, watermark=False), fake_ctx
        )
        assert submitted[0].enable_watermark is False  # type: ignore[attr-defined]

    async def test_private_image_url_returns_sanitized_error_without_submitting(
        self, fake_ctx: FakeContext, submitted: list[object]
    ) -> None:
        result = await omnihuman_create_task(
            OmniHumanCreateTaskInput(image="https://10.0.0.1/face.png", audio=AUDIO_URL), fake_ctx
        )

        assert isinstance(result, ToolResult)
        assert result.is_error
        assert "10.0.0.1" not in result.content[0].text
        assert submitted == []

    async def test_artifact_reference_requires_presignable_store(
        self, fake_ctx: FakeContext, submitted: list[object]
    ) -> None:
        result = await omnihuman_create_task(
            OmniHumanCreateTaskInput(
                image=f"seed-media://artifacts/{ARTIFACT_ID}", audio=AUDIO_URL
            ),
            fake_ctx,
        )

        assert isinstance(result, ToolResult)
        assert result.is_error
        assert "media_upload" in result.content[0].text
        assert submitted == []

    async def test_artifact_references_are_presigned_and_submitted(
        self,
        fake_ctx: FakeContext,
        submitted: list[object],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        runtime = fake_ctx.lifespan_context["runtime"]

        async def presign(
            artifact_id: str, *, expires_in_seconds: int, auth: AuthContext | None = None
        ) -> PresignedArtifact:
            ref = ArtifactRef(
                id=artifact_id,
                uri=f"seed-media://artifacts/{artifact_id}",
                media_type=MediaType.IMAGE,
                mime_type="image/png",
                bytes=2048,
                created_at="2026-10-15T00:00:00+00:00",
            )
            return PresignedArtifact(url="https://store.example.com/face.png?sig=1", ref=ref)

        monkeypatch.setattr(runtime.artifact_store, "presign", presign, raising=False)

        result = await omnihuman_create_task(
            OmniHumanCreateTaskInput(
                image=f"seed-media://artifacts/{ARTIFACT_ID}", audio=AUDIO_URL
            ),
            fake_ctx,
        )

        assert isinstance(result, OmniHumanCreateTaskOutput)
        assert str(submitted[0].image_url) == "https://store.example.com/face.png?sig=1"  # type: ignore[attr-defined]

    async def test_presign_provider_failure_is_a_classified_error_result(
        self,
        fake_ctx: FakeContext,
        submitted: list[object],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        runtime = fake_ctx.lifespan_context["runtime"]

        async def presign(*_args: object, **_kwargs: object) -> PresignedArtifact:
            raise provider_error(retryable=True, code="SIGNING_FAILED")

        monkeypatch.setattr(runtime.artifact_store, "presign", presign, raising=False)

        result = await omnihuman_create_task(
            OmniHumanCreateTaskInput(
                image=f"seed-media://artifacts/{ARTIFACT_ID}", audio=AUDIO_URL
            ),
            fake_ctx,
        )

        assert isinstance(result, ToolResult)
        assert result.is_error
        assert "SIGNING_FAILED" in result.content[0].text
        assert submitted == []

    async def test_provider_error_returns_error_result_and_records_nothing(
        self, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def create(_self: OmniHumanService, _request: object) -> OmniHumanSubmission:
            raise provider_error()

        monkeypatch.setattr(OmniHumanService, "create", create)
        monkeypatch.setattr(OmniHumanService, "close", _close)

        result = await omnihuman_create_task(
            OmniHumanCreateTaskInput(image=IMAGE_URL, audio=AUDIO_URL), fake_ctx
        )

        assert isinstance(result, ToolResult)
        assert result.is_error
        assert "50510" in result.content[0].text
        runtime = fake_ctx.lifespan_context["runtime"]
        assert await runtime.ownership_store.list_task_ids("vision-cv", AuthContext()) == set()

    async def test_rejects_when_not_configured(
        self, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from ark_mcp.config.env import get_settings

        monkeypatch.setenv("BYTEPLUS_OMNIHUMAN_ENABLED", "false")
        get_settings.cache_clear()
        runtime = fake_ctx.lifespan_context["runtime"]
        monkeypatch.setattr(runtime, "settings", get_settings())

        with pytest.raises(ValueError, match="BYTEPLUS_OMNIHUMAN_ENABLED"):
            await omnihuman_create_task(
                OmniHumanCreateTaskInput(image=IMAGE_URL, audio=AUDIO_URL), fake_ctx
            )

    def test_input_rejects_overlong_prompt(self) -> None:
        with pytest.raises(ValueError):
            OmniHumanCreateTaskInput(image=IMAGE_URL, audio=AUDIO_URL, prompt="x" * 2001)


class TestGet:
    @pytest.mark.parametrize("status", ["queued", "running"])
    async def test_in_progress_has_no_output(
        self, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch, status: str
    ) -> None:
        await record_owner(fake_ctx)
        stub_get(monkeypatch, OmniHumanTaskResult(status=status, request_id="r"))  # type: ignore[arg-type]

        result = await omnihuman_get_task(OmniHumanGetTaskInput(task_id="oh-1"), fake_ctx)

        assert isinstance(result, OmniHumanTaskOutput)
        assert result.status == status
        assert result.video is None
        assert result.source_url is None
        assert result.persistence == "not_applicable"

    @pytest.mark.parametrize("status", ["canceled", "expired", "not_found"])
    async def test_terminal_without_output(
        self, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch, status: str
    ) -> None:
        await record_owner(fake_ctx)
        stub_get(monkeypatch, OmniHumanTaskResult(status=status))  # type: ignore[arg-type]

        result = await omnihuman_get_task(OmniHumanGetTaskInput(task_id="oh-1"), fake_ctx)

        assert isinstance(result, OmniHumanTaskOutput)
        assert result.status == status
        assert result.persistence == "not_applicable"

    async def test_succeeded_persists_video_once(
        self, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await record_owner(fake_ctx)
        stub_get(monkeypatch, succeeded_result())
        runtime = fake_ctx.lifespan_context["runtime"]
        copies: list[str] = []

        async def copy_from_trusted_url(**kwargs: object) -> ArtifactRef:
            copies.append(str(kwargs["url"]))
            assert kwargs["media_type"] == MediaType.VIDEO
            assert kwargs["mime_type"] == "video/mp4"
            assert kwargs["extra_trusted_host_suffixes"] == (".tiktokcdn.com",)
            return ArtifactRef(
                id=ARTIFACT_ID,
                uri=f"seed-media://artifacts/{ARTIFACT_ID}",
                media_type=MediaType.VIDEO,
                mime_type="video/mp4",
                bytes=10,
                created_at="2026-10-15T00:00:00+00:00",
            )

        monkeypatch.setattr(runtime.artifact_store, "copy_from_trusted_url", copy_from_trusted_url)

        first = await omnihuman_get_task(OmniHumanGetTaskInput(task_id="oh-1"), fake_ctx)
        second = await omnihuman_get_task(OmniHumanGetTaskInput(task_id="oh-1"), fake_ctx)

        assert isinstance(first, OmniHumanTaskOutput)
        assert isinstance(second, OmniHumanTaskOutput)
        assert first.persistence == second.persistence == "persisted"
        assert first.video is not None
        assert second.video is not None
        assert second.video.id == first.video.id
        assert first.source_url is not None
        assert copies == [VIDEO_URL]

    async def test_persistence_failure_preserves_source_url(
        self, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await record_owner(fake_ctx)
        stub_get(monkeypatch, succeeded_result())
        runtime = fake_ctx.lifespan_context["runtime"]

        async def copy_from_trusted_url(**_kwargs: object) -> ArtifactRef:
            raise ArtifactPersistenceError("storage_failed", "Could not store.", retryable=True)

        monkeypatch.setattr(runtime.artifact_store, "copy_from_trusted_url", copy_from_trusted_url)

        result = await omnihuman_get_task(OmniHumanGetTaskInput(task_id="oh-1"), fake_ctx)

        assert isinstance(result, OmniHumanTaskOutput)
        assert result.status == "succeeded"
        assert result.persistence == "failed"
        assert result.persistence_issue is not None
        assert result.persistence_issue.code == "storage_failed"
        assert str(result.source_url) == VIDEO_URL
        assert result.video is None

    async def test_persist_output_false_skips_storage(
        self, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await record_owner(fake_ctx)
        stub_get(monkeypatch, succeeded_result())

        result = await omnihuman_get_task(
            OmniHumanGetTaskInput(task_id="oh-1", persist_output=False), fake_ctx
        )

        assert isinstance(result, OmniHumanTaskOutput)
        assert result.persistence == "not_requested"
        assert str(result.source_url) == VIDEO_URL

    async def test_persist_output_requires_task_execution(
        self, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await record_owner(fake_ctx)
        stub_get(monkeypatch, succeeded_result())
        fake_ctx.task_id = None

        result = await omnihuman_get_task(OmniHumanGetTaskInput(task_id="oh-1"), fake_ctx)

        assert isinstance(result, ToolResult)
        assert result.is_error

    async def test_failed_task_reports_provider_failure(
        self, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await record_owner(fake_ctx)
        stub_get(
            monkeypatch,
            OmniHumanTaskResult(status="failed", error_code="50510", error_message="Risk"),
        )

        result = await omnihuman_get_task(OmniHumanGetTaskInput(task_id="oh-1"), fake_ctx)

        assert isinstance(result, OmniHumanTaskOutput)
        assert result.status == "failed"
        assert result.error is not None
        assert result.error.code == "50510"
        assert result.error.message == "Risk"

    async def test_unknown_task_is_rejected_for_non_local_principal(
        self, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub_get(monkeypatch, succeeded_result())
        runtime = fake_ctx.lifespan_context["runtime"]
        await runtime.ownership_store.record(
            "vision-cv", "oh-1", AuthContext(principal_id="bob", tenant_id="t", transport="http")
        )
        from ark_mcp.tools import omnihuman_get_task as module

        monkeypatch.setattr(
            module, "get_principal", lambda _ctx: AuthContext(principal_id="eve", tenant_id="t")
        )

        with pytest.raises(PermissionError):
            await omnihuman_get_task(OmniHumanGetTaskInput(task_id="oh-1"), fake_ctx)

    async def test_provider_error_returns_error_result(
        self, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await record_owner(fake_ctx)
        stub_get(monkeypatch, provider_error(code="50400"))

        result = await omnihuman_get_task(OmniHumanGetTaskInput(task_id="oh-1"), fake_ctx)

        assert isinstance(result, ToolResult)
        assert result.is_error


class TestCancel:
    async def test_cancel_success(
        self, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await record_owner(fake_ctx)

        async def cancel(_self: OmniHumanService, task_id: str) -> OmniHumanCancelResult:
            assert task_id == "oh-1"
            return OmniHumanCancelResult(canceled=True, request_id="r")

        monkeypatch.setattr(OmniHumanService, "cancel", cancel)
        monkeypatch.setattr(OmniHumanService, "close", _close)

        result = await omnihuman_cancel_task(OmniHumanCancelTaskInput(task_id="oh-1"), fake_ctx)

        assert isinstance(result, OmniHumanCancelTaskOutput)
        assert result.canceled is True
        assert result.task_id == "oh-1"

    async def test_already_started_is_a_normal_result(
        self, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await record_owner(fake_ctx)

        async def cancel(_self: OmniHumanService, _task_id: str) -> OmniHumanCancelResult:
            return OmniHumanCancelResult(canceled=False, message="already started")

        monkeypatch.setattr(OmniHumanService, "cancel", cancel)
        monkeypatch.setattr(OmniHumanService, "close", _close)

        result = await omnihuman_cancel_task(OmniHumanCancelTaskInput(task_id="oh-1"), fake_ctx)

        assert isinstance(result, OmniHumanCancelTaskOutput)
        assert result.canceled is False
        assert result.message == "already started"

    async def test_other_principal_cannot_cancel_and_nothing_is_sent(
        self, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runtime = fake_ctx.lifespan_context["runtime"]
        await runtime.ownership_store.record(
            "vision-cv", "oh-1", AuthContext(principal_id="bob", tenant_id="t", transport="http")
        )
        called: list[str] = []

        async def cancel(_self: OmniHumanService, task_id: str) -> OmniHumanCancelResult:
            called.append(task_id)
            return OmniHumanCancelResult(canceled=True)

        monkeypatch.setattr(OmniHumanService, "cancel", cancel)
        monkeypatch.setattr(OmniHumanService, "close", _close)
        from ark_mcp.tools import omnihuman_cancel_task as module

        monkeypatch.setattr(
            module, "get_principal", lambda _ctx: AuthContext(principal_id="eve", tenant_id="t")
        )

        with pytest.raises(PermissionError):
            await omnihuman_cancel_task(OmniHumanCancelTaskInput(task_id="oh-1"), fake_ctx)

        assert called == []

    async def test_provider_error_returns_error_result(
        self, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await record_owner(fake_ctx)

        async def cancel(_self: OmniHumanService, _task_id: str) -> OmniHumanCancelResult:
            raise provider_error(code="50400")

        monkeypatch.setattr(OmniHumanService, "cancel", cancel)
        monkeypatch.setattr(OmniHumanService, "close", _close)

        result = await omnihuman_cancel_task(OmniHumanCancelTaskInput(task_id="oh-1"), fake_ctx)

        assert isinstance(result, ToolResult)
        assert result.is_error


class TestOutputInvariants:
    def test_persisted_state_requires_a_video(self) -> None:
        with pytest.raises(ValueError, match="persisted"):
            OmniHumanTaskOutput(
                task_id="oh-1",
                status="succeeded",
                source_url=VIDEO_URL,
                persistence="persisted",
            )

    def test_failed_persistence_requires_an_issue(self) -> None:
        with pytest.raises(ValueError, match="persistence"):
            OmniHumanTaskOutput(
                task_id="oh-1",
                status="succeeded",
                source_url=VIDEO_URL,
                persistence="failed",
            )
