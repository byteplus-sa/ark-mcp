"""Integration tests for the whitelist-only Seedance 2.5 Premium tools."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest

from ark_mcp.providers.modelark.seedance import SeedanceService
from ark_mcp.tools.seedance_2_5_create_task import (
    Seedance25CreateTaskInput,
    Seedance25CreateTaskOutput,
    seedance_2_5_create_task,
)
from ark_mcp.tools.seedance_2_5_premium_create_task import (
    Seedance25PremiumCreateTaskInput,
    seedance_2_5_premium_create_task,
)
from ark_mcp.tools.seedance_2_5_premium_create_task_variations import (
    Seedance25PremiumVariationsInput,
    Seedance25VariationsOutput,
    seedance_2_5_premium_create_task_variations,
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


class TestSeedance25PremiumCreateTaskTool:
    async def test_create_task_4k_uses_premium_model(
        self, premium_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def mock_create(self: SeedanceService, request: Any) -> tuple[str, str | None]:
            assert request.model == PREMIUM_MODEL
            assert request.resolution == "4k"
            return "task-premium", "req-premium"

        monkeypatch.setattr(SeedanceService, "create_task", mock_create)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        result = await seedance_2_5_premium_create_task(
            Seedance25PremiumCreateTaskInput(prompt="a 4k aerial shot", resolution="4k"),
            premium_ctx,
        )

        assert isinstance(result, Seedance25CreateTaskOutput)
        assert result.task_id == "task-premium"

    async def test_regular_2_5_model_rejected(self, premium_ctx: FakeContext) -> None:
        with pytest.raises(ValueError, match=r"not a Seedance 2\.5 Premium model"):
            await seedance_2_5_premium_create_task(
                Seedance25PremiumCreateTaskInput(prompt="test", model=REGULAR_MODEL),
                premium_ctx,
            )

    async def test_regular_2_5_tool_rejects_premium_model(self, premium_ctx: FakeContext) -> None:
        with pytest.raises(ValueError, match="seedance_2_5_premium_create_task"):
            await seedance_2_5_create_task(
                Seedance25CreateTaskInput(prompt="test", model=PREMIUM_MODEL),
                premium_ctx,
            )

    async def test_regular_2_5_tool_default_is_not_premium(
        self, premium_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def mock_create(self: SeedanceService, request: Any) -> tuple[str, str | None]:
            assert request.model == REGULAR_MODEL
            return "task-regular", "req-regular"

        monkeypatch.setattr(SeedanceService, "create_task", mock_create)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        result = await seedance_2_5_create_task(
            Seedance25CreateTaskInput(prompt="test"), premium_ctx
        )
        assert isinstance(result, Seedance25CreateTaskOutput)
        assert result.task_id == "task-regular"

    async def test_flag_off_rejects_call(self, test_env: None, fake_ctx: FakeContext) -> None:
        with pytest.raises(ValueError, match="whitelist-only and disabled"):
            await seedance_2_5_premium_create_task(
                Seedance25PremiumCreateTaskInput(prompt="test", resolution="4k"),
                fake_ctx,
            )


class TestSeedance25PremiumVariationsTool:
    async def test_variations_success(
        self, premium_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = 0

        async def mock_create(self: SeedanceService, request: Any) -> tuple[str, str | None]:
            nonlocal calls
            calls += 1
            assert request.model == PREMIUM_MODEL
            assert request.resolution == "4k"
            return f"task-{calls}", f"req-{calls}"

        monkeypatch.setattr(SeedanceService, "create_task", mock_create)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        result = await seedance_2_5_premium_create_task_variations(
            Seedance25PremiumVariationsInput(variations=2, prompt="4k variations", resolution="4k"),
            premium_ctx,
        )

        assert isinstance(result, Seedance25VariationsOutput)
        assert result.summary.succeeded == 2

    async def test_flag_off_rejects_call(self, test_env: None, fake_ctx: FakeContext) -> None:
        with pytest.raises(ValueError, match="whitelist-only and disabled"):
            await seedance_2_5_premium_create_task_variations(
                Seedance25PremiumVariationsInput(variations=1, prompt="test"),
                fake_ctx,
            )
