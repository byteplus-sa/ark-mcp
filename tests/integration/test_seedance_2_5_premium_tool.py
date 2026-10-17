"""Integration tests for the whitelist-only Seedance 2.5 Premium tools."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest

from ark_mcp.providers.modelark.generation_tasks import GenerationTaskService
from ark_mcp.providers.modelark.schemas import GenerationTaskResponse
from ark_mcp.providers.modelark.seedance import SeedanceService
from ark_mcp.tools.seedance_create_task import (
    SeedanceCreateTaskInput,
    SeedanceCreateTaskOutput,
    seedance_create_task,
)
from tests.fixtures.fake_context import FakeContext

PREMIUM_MODEL = "dreamina-seedance-2-5-premium-260915"
REGULAR_MODEL = "dreamina-seedance-2-5-260628"


async def _mock_close(self: SeedanceService) -> None:
    pass


@pytest.fixture
def premium_env(test_env: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Configure 2.0 + 2.5 bindings with the Premium flag on."""
    monkeypatch.setenv(
        "SEEDANCE_MODEL_BINDINGS",
        '[{"model_id":"dreamina-seedance-2-0-260128","family":"standard"},'
        f'{{"model_id":"{REGULAR_MODEL}","family":"seedance_2_5"}}]',
    )
    monkeypatch.setenv("BYTEPLUS_MODELARK_SEEDANCE_2_5_PREMIUM_ENABLED", "true")

    from ark_mcp.config.env import get_settings
    from ark_mcp.config.model_capabilities import refresh_capability_registry

    get_settings.cache_clear()
    refresh_capability_registry()
    yield
    get_settings.cache_clear()
    refresh_capability_registry()


@pytest.fixture
async def premium_ctx(premium_env: None) -> AsyncIterator[FakeContext]:
    from ark_mcp.config.env import get_settings
    from ark_mcp.runtime import close_runtime_services, create_runtime_services

    runtime = await create_runtime_services(get_settings())
    try:
        yield FakeContext(lifespan_context={"runtime": runtime})
    finally:
        await close_runtime_services(runtime)


def _capture_creates(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    captured: list[Any] = []

    async def mock_create(self: SeedanceService, request: Any) -> tuple[str, str | None]:
        captured.append(request)
        return f"task-{len(captured)}", None

    monkeypatch.setattr(SeedanceService, "create_task", mock_create)
    monkeypatch.setattr(SeedanceService, "close", _mock_close)
    return captured


class TestSeedance25PremiumCreateTask:
    async def test_4k_with_premium_model_id(
        self, premium_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _capture_creates(monkeypatch)
        result = await seedance_create_task(
            SeedanceCreateTaskInput(
                prompt="a 4k aerial shot", model=PREMIUM_MODEL, resolution="4k"
            ),
            premium_ctx,
        )
        assert isinstance(result, SeedanceCreateTaskOutput)
        assert (result.model, result.family) == (PREMIUM_MODEL, "seedance_2_5_premium")
        assert (captured[0].model, captured[0].resolution) == (PREMIUM_MODEL, "4k")

    async def test_regular_2_5_rejects_4k_and_points_to_discovery(
        self, premium_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _capture_creates(monkeypatch)
        with pytest.raises(ValueError) as excinfo:
            await seedance_create_task(
                SeedanceCreateTaskInput(prompt="x", model=REGULAR_MODEL, resolution="4k"),
                premium_ctx,
            )
        message = str(excinfo.value)
        assert "resolution '4k' is not supported" in message
        assert "ark_list_models(kind='video')" in message
        assert captured == []

    async def test_flag_off_leaves_premium_unconfigured(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _capture_creates(monkeypatch)
        with pytest.raises(ValueError, match="is not configured"):
            await seedance_create_task(
                SeedanceCreateTaskInput(prompt="x", model=PREMIUM_MODEL), fake_ctx
            )
        assert captured == []

    async def test_premium_draft_sends_480p(
        self, premium_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _capture_creates(monkeypatch)
        await seedance_create_task(
            SeedanceCreateTaskInput(prompt="a 4k aerial shot", model=PREMIUM_MODEL, draft=True),
            premium_ctx,
        )
        assert (captured[0].model, captured[0].draft, captured[0].resolution) == (
            PREMIUM_MODEL,
            True,
            "480p",
        )

    async def test_premium_variations(
        self, premium_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _capture_creates(monkeypatch)
        result = await seedance_create_task(
            SeedanceCreateTaskInput(
                model=PREMIUM_MODEL, variations=2, variation_prompts=["a", "b"], resolution="4k"
            ),
            premium_ctx,
        )
        assert isinstance(result, SeedanceCreateTaskOutput)
        assert result.submitted == 2
        assert {request.model for request in captured} == {PREMIUM_MODEL}


class TestSeedance25PremiumDraftMode:
    async def test_final_from_premium_draft_uses_premium_model(
        self, premium_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: list[Any] = []

        async def mock_get(self: SeedanceService, task_id: str) -> tuple[Any, str | None]:
            return GenerationTaskResponse(
                id=task_id, model=PREMIUM_MODEL, status="succeeded", draft=True
            ), None

        async def mock_create(self: SeedanceService, request: Any) -> tuple[str, str | None]:
            captured.append(request)
            return "cgt-premium-final", None

        monkeypatch.setattr(GenerationTaskService, "get_task", mock_get)

        monkeypatch.setattr(GenerationTaskService, "close", _mock_close)
        monkeypatch.setattr(SeedanceService, "create_task", mock_create)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        result = await seedance_create_task(
            SeedanceCreateTaskInput(draft_task_id="cgt-premium-draft"), premium_ctx
        )

        assert isinstance(result, SeedanceCreateTaskOutput)
        assert captured[0].model == PREMIUM_MODEL
        assert captured[0].content[0].draft_task == {"id": "cgt-premium-draft"}
        assert captured[0].draft is None
        assert captured[0].resolution == "4k"
