"""Integration coverage for 4K on the unified Seedance 2.5 model."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest

from ark_mcp.config.env import get_settings
from ark_mcp.config.model_capabilities import refresh_capability_registry
from ark_mcp.providers.modelark.schemas import SeedanceCreateProviderRequest
from ark_mcp.providers.modelark.seedance import SeedanceService
from ark_mcp.runtime import close_runtime_services, create_runtime_services
from ark_mcp.tools.seedance_create_task import (
    SeedanceCreateTaskInput,
    SeedanceCreateTaskOutput,
    seedance_create_task,
)
from tests.fixtures.fake_context import FakeContext

MODEL = "dreamina-seedance-2-5-260628"


@pytest.fixture
def seedance_4k_env(test_env: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("SEEDANCE_DEFAULT_MODEL", MODEL)
    monkeypatch.setenv(
        "SEEDANCE_MODEL_BINDINGS", f'[{{"model_id":"{MODEL}","family":"seedance_2_5"}}]'
    )
    get_settings.cache_clear()
    refresh_capability_registry()
    yield
    get_settings.cache_clear()
    refresh_capability_registry()


@pytest.fixture
async def seedance_4k_ctx(seedance_4k_env: None) -> AsyncIterator[FakeContext]:
    runtime = await create_runtime_services(get_settings())
    try:
        yield FakeContext(lifespan_context={"runtime": runtime})
    finally:
        await close_runtime_services(runtime)


@pytest.fixture
def captured_creates(
    monkeypatch: pytest.MonkeyPatch,
) -> list[SeedanceCreateProviderRequest]:
    captured: list[SeedanceCreateProviderRequest] = []

    async def create(
        self: SeedanceService, request: SeedanceCreateProviderRequest
    ) -> tuple[str, str | None]:
        captured.append(request)
        return f"task-{len(captured)}", None

    monkeypatch.setattr(SeedanceService, "create_task", create)
    return captured


@pytest.mark.parametrize("model", [None, MODEL])
@pytest.mark.parametrize("resolution", ["480p", "720p", "1080p", "4k"])
async def test_all_resolutions_use_the_same_2_5_model(
    seedance_4k_ctx: FakeContext,
    captured_creates: list[SeedanceCreateProviderRequest],
    model: str | None,
    resolution: str,
) -> None:
    result = await seedance_create_task(
        SeedanceCreateTaskInput.model_validate(
            {
                "prompt": "A cinematic aerial shot",
                "model": model,
                "resolution": resolution,
                "duration": 30,
            }
        ),
        seedance_4k_ctx,
    )
    assert isinstance(result, SeedanceCreateTaskOutput)
    assert (result.model, result.family, result.submitted) == (MODEL, "seedance_2_5", 1)
    assert (captured_creates[0].model, captured_creates[0].resolution) == (MODEL, resolution)
    assert captured_creates[0].duration == 30


async def test_4k_dry_run_never_submits(
    seedance_4k_ctx: FakeContext,
    captured_creates: list[SeedanceCreateProviderRequest],
) -> None:
    result = await seedance_create_task(
        SeedanceCreateTaskInput(prompt="An aerial shot", resolution="4k", dry_run=True),
        seedance_4k_ctx,
    )
    assert isinstance(result, SeedanceCreateTaskOutput)
    assert result.preview is not None
    assert result.preview.requests[0]["model"] == MODEL
    assert result.preview.requests[0]["resolution"] == "4k"
    assert result.tasks == []
    assert captured_creates == []


async def test_4k_variations_preserve_resolution_on_every_request(
    seedance_4k_ctx: FakeContext,
    captured_creates: list[SeedanceCreateProviderRequest],
) -> None:
    result = await seedance_create_task(
        SeedanceCreateTaskInput(
            resolution="4k", variations=2, variation_prompts=["A sunrise", "A sunset"]
        ),
        seedance_4k_ctx,
    )
    assert isinstance(result, SeedanceCreateTaskOutput)
    assert (result.submitted, result.failed) == (2, 0)
    assert len(captured_creates) == 2
    assert {(request.model, request.resolution) for request in captured_creates} == {(MODEL, "4k")}


async def test_retired_premium_id_is_not_automatically_bound(
    seedance_4k_ctx: FakeContext,
    captured_creates: list[SeedanceCreateProviderRequest],
) -> None:
    with pytest.raises(ValueError, match="is not configured"):
        await seedance_create_task(
            SeedanceCreateTaskInput(
                prompt="An aerial shot", model="dreamina-seedance-2-5-premium-260915"
            ),
            seedance_4k_ctx,
        )
    assert captured_creates == []


async def test_custom_2_5_endpoint_supports_4k(
    seedance_4k_ctx: FakeContext,
    captured_creates: list[SeedanceCreateProviderRequest],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "SEEDANCE_MODEL_BINDINGS",
        f'[{{"model_id":"{MODEL}","family":"seedance_2_5"}},'
        '{"model_id":"ep-video","family":"seedance_2_5"}]',
    )
    get_settings.cache_clear()
    refresh_capability_registry()
    result = await seedance_create_task(
        SeedanceCreateTaskInput(prompt="An aerial shot", model="ep-video", resolution="4k"),
        seedance_4k_ctx,
    )
    assert isinstance(result, SeedanceCreateTaskOutput)
    assert (captured_creates[0].model, captured_creates[0].resolution) == ("ep-video", "4k")


@pytest.mark.parametrize("family", ["fast", "mini"])
async def test_4k_remains_rejected_for_2_0_fast_and_mini(
    seedance_4k_ctx: FakeContext,
    captured_creates: list[SeedanceCreateProviderRequest],
    monkeypatch: pytest.MonkeyPatch,
    family: str,
) -> None:
    monkeypatch.setenv(
        "SEEDANCE_MODEL_BINDINGS",
        f'[{{"model_id":"{MODEL}","family":"seedance_2_5"}},'
        f'{{"model_id":"ep-limited","family":"{family}"}}]',
    )
    get_settings.cache_clear()
    refresh_capability_registry()
    with pytest.raises(ValueError, match="resolution '4k' is not supported"):
        await seedance_create_task(
            SeedanceCreateTaskInput(prompt="An aerial shot", model="ep-limited", resolution="4k"),
            seedance_4k_ctx,
        )
    assert captured_creates == []
