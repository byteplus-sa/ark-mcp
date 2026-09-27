"""Integration tests for the Seedance 2.5 tool handlers.

Exercises create and variations through the full tool path with mocked
provider responses and a temp artifact store.
"""

from __future__ import annotations

from typing import Any

import pytest

from ark_mcp.providers.modelark.seedance import SeedanceService
from ark_mcp.tools._seedance_shared import (
    SeedanceAudioInput,
    SeedanceImageInput,
    SeedanceVideoInput,
)
from ark_mcp.tools.seedance_2_5_create_task import (
    Seedance25CreateTaskInput,
    Seedance25CreateTaskOutput,
    seedance_2_5_create_task,
)
from ark_mcp.tools.seedance_2_5_create_task_variations import (
    Seedance25VariationsInput,
    Seedance25VariationsOutput,
    seedance_2_5_create_task_variations,
)
from tests.fixtures.fake_context import FakeContext


async def _mock_close(self: SeedanceService) -> None:
    pass


@pytest.fixture
def seedance_2_5_env(test_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure environment with both Seedance 2.0 and 2.5 model bindings."""
    monkeypatch.setenv(
        "SEEDANCE_MODEL_BINDINGS",
        '[{"model_id":"dreamina-seedance-2-0-260128","family":"standard"},'
        '{"model_id":"dreamina-seedance-2-5-260628","family":"seedance_2_5"}]',
    )
    monkeypatch.setenv("SEEDANCE_DEFAULT_MODEL", "dreamina-seedance-2-5-260628")
    monkeypatch.setenv("SEEDANCE_MODEL_FAMILY", "seedance_2_5")

    from ark_mcp.config.env import get_settings
    from ark_mcp.config.model_capabilities import refresh_capability_registry

    get_settings.cache_clear()
    refresh_capability_registry()

    yield

    get_settings.cache_clear()
    refresh_capability_registry()


@pytest.fixture
async def seedance_2_5_ctx(seedance_2_5_env: None) -> FakeContext:
    from ark_mcp.config.env import get_settings
    from ark_mcp.runtime import close_runtime_services, create_runtime_services
    from tests.fixtures.fake_context import FakeContext

    runtime = await create_runtime_services(get_settings())
    try:
        yield FakeContext(lifespan_context={"runtime": runtime})
    finally:
        await close_runtime_services(runtime)


class TestSeedance25CreateTaskTool:
    """Integration tests for seedance_2_5_create_task."""

    async def test_create_task_success(
        self,
        seedance_2_5_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def mock_create(self: SeedanceService, request: Any) -> tuple[str, str | None]:
            assert request.model == "dreamina-seedance-2-5-260628"
            return "task-2-5-abc", "req-2-5"

        monkeypatch.setattr(SeedanceService, "create_task", mock_create)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        result = await seedance_2_5_create_task(
            Seedance25CreateTaskInput(
                prompt="a cinematic 30-second film",
                duration=25,
                resolution="720p",
            ),
            seedance_2_5_ctx,
        )

        assert isinstance(result, Seedance25CreateTaskOutput)
        assert result.task_id == "task-2-5-abc"
        assert result.status == "queued"

    async def test_create_task_1080p_passthrough(
        self,
        seedance_2_5_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def mock_create(self: SeedanceService, request: Any) -> tuple[str, str | None]:
            assert request.model == "dreamina-seedance-2-5-260628"
            assert request.resolution == "1080p"
            return "task-1080p", "req-1080p"

        monkeypatch.setattr(SeedanceService, "create_task", mock_create)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        result = await seedance_2_5_create_task(
            Seedance25CreateTaskInput(
                prompt="a cinematic 30-second film",
                resolution="1080p",
            ),
            seedance_2_5_ctx,
        )

        assert isinstance(result, Seedance25CreateTaskOutput)
        assert result.task_id == "task-1080p"
        assert result.status == "queued"

    async def test_create_task_with_max_references(
        self,
        seedance_2_5_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def mock_create(self: SeedanceService, request: Any) -> tuple[str, str | None]:
            return "task-refs", "req-refs"

        monkeypatch.setattr(SeedanceService, "create_task", mock_create)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        images = [
            SeedanceImageInput(kind="url", url=f"https://example.com/img{i}.jpg") for i in range(15)
        ]
        videos = [SeedanceVideoInput(url=f"https://example.com/vid{i}.mp4") for i in range(5)]

        result = await seedance_2_5_create_task(
            Seedance25CreateTaskInput(
                prompt="multi-reference generation",
                images=images,
                videos=videos,
                duration=20,
            ),
            seedance_2_5_ctx,
        )

        assert isinstance(result, Seedance25CreateTaskOutput)
        assert result.task_id == "task-refs"

    async def test_create_task_audio_only(
        self,
        seedance_2_5_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Seedance 2.5 supports audio-only input (unique to 2.5)."""

        async def mock_create(self: SeedanceService, request: Any) -> tuple[str, str | None]:
            return "task-audio", "req-audio"

        monkeypatch.setattr(SeedanceService, "create_task", mock_create)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        result = await seedance_2_5_create_task(
            Seedance25CreateTaskInput(
                prompt="visualize the music",
                audios=[SeedanceAudioInput(kind="url", url="https://example.com/song.mp3")],
                duration=10,
            ),
            seedance_2_5_ctx,
        )

        assert isinstance(result, Seedance25CreateTaskOutput)
        assert result.task_id == "task-audio"

    async def test_create_task_no_2_5_model_configured(
        self,
        test_env: None,
        fake_ctx: FakeContext,
    ) -> None:
        """When no 2.5 model is in bindings, the tool raises a clear error."""
        with pytest.raises(ValueError, match=r"No Seedance 2\.5 model is configured"):
            await seedance_2_5_create_task(
                Seedance25CreateTaskInput(prompt="test"),
                fake_ctx,
            )

    async def test_create_task_2_0_model_rejected(
        self,
        seedance_2_5_ctx: FakeContext,
    ) -> None:
        """Passing a 2.0 model ID to the 2.5 tool raises an error."""
        with pytest.raises(ValueError, match=r"not a Seedance 2\.5 model"):
            await seedance_2_5_create_task(
                Seedance25CreateTaskInput(
                    prompt="test",
                    model="dreamina-seedance-2-0-260128",
                ),
                seedance_2_5_ctx,
            )

    async def test_create_task_passes_omni_reference_task_type(
        self,
        seedance_2_5_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured_request: list[Any] = []

        async def mock_create(self: SeedanceService, request: Any) -> tuple[str, str | None]:
            captured_request.append(request)
            return "task-edit-2-5", "req-edit-2-5"

        monkeypatch.setattr(SeedanceService, "create_task", mock_create)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        result = await seedance_2_5_create_task(
            Seedance25CreateTaskInput(
                prompt="extend the video with a sunset scene",
                videos=[SeedanceVideoInput(url="https://example.com/source.mp4")],
                omni_reference_task_type="extend_video",
            ),
            seedance_2_5_ctx,
        )

        assert isinstance(result, Seedance25CreateTaskOutput)
        assert captured_request[0].omni_reference_task_type == "extend_video"


class TestSeedance25CreateTaskVariationsTool:
    """Integration tests for seedance_2_5_create_task_variations."""

    async def test_variations_success(
        self,
        seedance_2_5_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        call_count = 0

        async def mock_create(self: SeedanceService, request: Any) -> tuple[str, str | None]:
            nonlocal call_count
            call_count += 1
            assert request.model == "dreamina-seedance-2-5-260628"
            return f"task-var-{call_count}", f"req-var-{call_count}"

        monkeypatch.setattr(SeedanceService, "create_task", mock_create)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        result = await seedance_2_5_create_task_variations(
            Seedance25VariationsInput(
                variations=3,
                variation_prompts=["prompt a", "prompt b", "prompt c"],
                duration=20,
            ),
            seedance_2_5_ctx,
        )

        assert isinstance(result, Seedance25VariationsOutput)
        assert result.summary.total == 3
        assert result.summary.succeeded == 3
        assert result.summary.failed == 0

    async def test_variations_no_2_5_model_configured(
        self,
        test_env: None,
        fake_ctx: FakeContext,
    ) -> None:
        """When no 2.5 model is in bindings, the variations tool raises."""
        with pytest.raises(ValueError, match=r"No Seedance 2\.5 model is configured"):
            await seedance_2_5_create_task_variations(
                Seedance25VariationsInput(variations=1, prompt="test"),
                fake_ctx,
            )

    async def test_variations_2_0_model_rejected(
        self,
        seedance_2_5_ctx: FakeContext,
    ) -> None:
        """Passing a 2.0 model ID to the 2.5 variations tool raises an error."""
        with pytest.raises(
            ValueError,
            match=r"not a Seedance 2\.5 model\. Use seedance_create_task_variations for",
        ):
            await seedance_2_5_create_task_variations(
                Seedance25VariationsInput(
                    variations=1,
                    prompt="test",
                    model="dreamina-seedance-2-0-260128",
                ),
                seedance_2_5_ctx,
            )
