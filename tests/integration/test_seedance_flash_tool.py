"""Integration coverage for Seedance 2.5 Flash on the unified create tool."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from fastmcp import Client

from ark_mcp.config.env import get_settings
from ark_mcp.config.model_capabilities import get_capability_registry, refresh_capability_registry
from ark_mcp.providers.modelark.schemas import GenerationTaskResponse, SeedanceCreateProviderRequest
from ark_mcp.providers.modelark.seedance import SeedanceService
from ark_mcp.runtime import close_runtime_services, create_runtime_services
from ark_mcp.server import create_server
from ark_mcp.tools.seedance_create_task import (
    SeedanceCreateTaskInput,
    SeedanceCreateTaskOutput,
    seedance_create_task,
)
from tests.fixtures.fake_context import FakeContext

MODEL = "dreamina-seedance-2-5-flash-261115"


@pytest.fixture
def flash_env(test_env: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("SEEDANCE_DEFAULT_MODEL", "dreamina-seedance-2-5-260628")
    monkeypatch.setenv("SEEDANCE_MODEL_BINDINGS", "[]")
    get_settings.cache_clear()
    refresh_capability_registry()
    yield
    get_settings.cache_clear()
    refresh_capability_registry()


@pytest.fixture
async def flash_ctx(flash_env: None) -> AsyncIterator[FakeContext]:
    runtime = await create_runtime_services(get_settings())
    try:
        yield FakeContext(lifespan_context={"runtime": runtime})
    finally:
        await close_runtime_services(runtime)


@pytest.fixture
def captured_creates(monkeypatch: pytest.MonkeyPatch) -> list[SeedanceCreateProviderRequest]:
    captured: list[SeedanceCreateProviderRequest] = []

    async def create(
        self: SeedanceService, request: SeedanceCreateProviderRequest
    ) -> tuple[str, str | None]:
        captured.append(request)
        return f"flash-task-{len(captured)}", None

    monkeypatch.setattr(SeedanceService, "create_task", create)
    return captured


async def test_flash_discovery_shares_the_existing_tool_and_keeps_regular_default(
    flash_env: None,
) -> None:
    async with Client(create_server(get_settings())) as client:
        result = await client.call_tool("ark_list_models", {"input": {"kind": "video"}})
        payload = result.structured_content
        tools = await client.list_tools()
    assert payload is not None
    models = {model["model_id"]: model for model in payload["models"]}
    assert set(models) == {MODEL, "dreamina-seedance-2-5-260628"}
    flash = models[MODEL]
    assert (flash["family"], flash["tool"], flash["is_default"]) == (
        "seedance_2_5_flash",
        "seedance_create_task",
        False,
    )
    assert models["dreamina-seedance-2-5-260628"]["is_default"] is True
    assert flash["video"]["resolutions"] == ["480p", "720p"]
    assert flash["video"]["duration_seconds"] == [4, 30]
    assert flash["video"]["supports_draft"] is False
    assert flash["video"]["draft_final_resolution"] is None
    assert flash["video"]["task_types"] == ["auto", "reference", "edit", "extend"]
    assert flash["video"]["output_formats"] == ["mp4", "mov"]
    assert not any("flash" in tool.name for tool in tools)


async def test_flash_forwards_the_supplied_multimodal_request(
    flash_ctx: FakeContext, captured_creates: list[SeedanceCreateProviderRequest]
) -> None:
    image_urls = [
        "https://ark-doc.tos-ap-southeast-1.bytepluses.com/doc_image/r2v_tea_pic1.jpg",
        "https://ark-doc.tos-ap-southeast-1.bytepluses.com/doc_image/r2v_tea_pic2.jpg",
    ]
    video_url = "https://ark-doc.tos-ap-southeast-1.bytepluses.com/doc_video/r2v_tea_video1.mp4"
    audio_url = "https://ark-doc.tos-ap-southeast-1.bytepluses.com/doc_audio/r2v_tea_audio1.mp3"
    result = await seedance_create_task(
        SeedanceCreateTaskInput(
            model=MODEL,
            prompt="Use Video 1 POV and Audio 1 for a fruit tea promotional ad.",
            images=image_urls,
            videos=[video_url],
            audios=[audio_url],
            generate_audio=True,
            ratio="16:9",
            duration=11,
            watermark=False,
        ),
        flash_ctx,
    )
    assert isinstance(result, SeedanceCreateTaskOutput)
    assert (result.model, result.family, result.submitted) == (MODEL, "seedance_2_5_flash", 1)
    body = captured_creates[0].model_dump(exclude_none=True)
    assert body["model"] == MODEL
    assert (body["duration"], body["ratio"], body["generate_audio"], body["watermark"]) == (
        11,
        "16:9",
        True,
        False,
    )
    assert body["content"][1:] == [
        {"type": "image_url", "image_url": {"url": url}, "role": "reference_image"}
        for url in image_urls
    ] + [
        {"type": "video_url", "video_url": {"url": video_url}, "role": "reference_video"},
        {"type": "audio_url", "audio_url": {"url": audio_url}, "role": "reference_audio"},
    ]


@pytest.mark.parametrize("resolution", [None, "480p", "720p"])
@pytest.mark.parametrize("duration", [None, -1, 4, 30])
async def test_flash_accepts_supported_resolution_and_duration_boundaries(
    flash_ctx: FakeContext,
    captured_creates: list[SeedanceCreateProviderRequest],
    resolution: str | None,
    duration: int | None,
) -> None:
    result = await seedance_create_task(
        SeedanceCreateTaskInput.model_validate(
            {
                "model": MODEL,
                "prompt": "A fruit tea ad",
                "resolution": resolution,
                "duration": duration,
                "dry_run": True,
            }
        ),
        flash_ctx,
    )
    assert isinstance(result, SeedanceCreateTaskOutput)
    assert result.preview is not None
    body = result.preview.requests[0]
    assert body.get("resolution") == resolution
    assert body.get("duration") == duration
    assert captured_creates == []


@pytest.mark.parametrize("resolution", ["1080p", "4k"])
async def test_flash_rejects_high_resolution_before_submission(
    flash_ctx: FakeContext,
    captured_creates: list[SeedanceCreateProviderRequest],
    resolution: str,
) -> None:
    with pytest.raises(ValueError, match=f"resolution '{resolution}' is not supported"):
        await seedance_create_task(
            SeedanceCreateTaskInput.model_validate(
                {
                    "model": MODEL,
                    "prompt": "A tea ad",
                    "resolution": resolution,
                }
            ),
            flash_ctx,
        )
    assert captured_creates == []


async def test_flash_rejects_draft_preview_before_submission(
    flash_ctx: FakeContext,
    captured_creates: list[SeedanceCreateProviderRequest],
) -> None:
    with pytest.raises(ValueError, match=r"Draft mode .* is not supported"):
        await seedance_create_task(
            SeedanceCreateTaskInput(model=MODEL, prompt="A tea ad", draft=True), flash_ctx
        )
    assert captured_creates == []


async def test_flash_rejects_draft_promotion_before_submission(
    flash_ctx: FakeContext,
    captured_creates: list[SeedanceCreateProviderRequest],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def load(task_id: str, ctx: FakeContext) -> GenerationTaskResponse:
        return GenerationTaskResponse(id=task_id, model=MODEL, draft=True, status="succeeded")

    monkeypatch.setattr("ark_mcp.tools.seedance_create_task.load_draft_task", load)
    with pytest.raises(ValueError, match=r"Draft mode .* is not supported"):
        await seedance_create_task(SeedanceCreateTaskInput(draft_task_id="draft-1"), flash_ctx)
    assert captured_creates == []


async def test_flash_variations_keep_the_same_model_and_resolution(
    flash_ctx: FakeContext,
    captured_creates: list[SeedanceCreateProviderRequest],
) -> None:
    result = await seedance_create_task(
        SeedanceCreateTaskInput(
            model=MODEL,
            resolution="720p",
            variations=2,
            variation_prompts=["A sunrise tea ad", "A sunset tea ad"],
        ),
        flash_ctx,
    )
    assert isinstance(result, SeedanceCreateTaskOutput)
    assert (result.submitted, result.failed) == (2, 0)
    assert len(captured_creates) == 2
    assert {(request.model, request.resolution) for request in captured_creates} == {
        (MODEL, "720p")
    }


@pytest.mark.parametrize("field,maximum", [("images", 30), ("videos", 10), ("audios", 10)])
async def test_flash_rejects_excess_references_before_submission(
    flash_ctx: FakeContext,
    captured_creates: list[SeedanceCreateProviderRequest],
    field: str,
    maximum: int,
) -> None:
    with pytest.raises(ValueError, match="at most"):
        request = SeedanceCreateTaskInput.model_validate(
            {
                "model": MODEL,
                field: ["https://cdn.example.com/reference"] * (maximum + 1),
            }
        )
        await seedance_create_task(request, flash_ctx)
    assert captured_creates == []


@pytest.mark.parametrize("duration", [0, 3, 31])
async def test_flash_rejects_invalid_durations_before_submission(
    flash_ctx: FakeContext,
    captured_creates: list[SeedanceCreateProviderRequest],
    duration: int,
) -> None:
    with pytest.raises(ValueError):
        request = SeedanceCreateTaskInput(model=MODEL, prompt="A tea ad", duration=duration)
        await seedance_create_task(request, flash_ctx)
    assert captured_creates == []


@pytest.mark.parametrize("task_type", ["auto", "reference", "edit", "extend"])
async def test_flash_supports_structured_task_types_and_mov(
    flash_ctx: FakeContext,
    captured_creates: list[SeedanceCreateProviderRequest],
    task_type: str,
) -> None:
    result = await seedance_create_task(
        SeedanceCreateTaskInput.model_validate(
            {
                "model": MODEL,
                "prompt": "Edit or continue the fruit tea ad.",
                "videos": ["https://cdn.example.com/tea.mp4"],
                "output_format": "mov",
                "omni_reference_task_type": task_type,
                "dry_run": True,
            }
        ),
        flash_ctx,
    )
    assert isinstance(result, SeedanceCreateTaskOutput)
    assert result.preview is not None
    assert result.preview.requests[0]["omni_reference_task_type"] == task_type
    assert result.preview.requests[0]["output_format"] == "mov"
    assert captured_creates == []


async def test_flash_supports_audio_only_input_and_reference_count_boundaries(
    flash_ctx: FakeContext,
    captured_creates: list[SeedanceCreateProviderRequest],
) -> None:
    for media in [
        {"audios": ["https://cdn.example.com/music.mp3"]},
        {
            "images": ["https://cdn.example.com/tea.jpg"] * 30,
            "videos": ["https://cdn.example.com/tea.mp4"] * 10,
            "audios": ["https://cdn.example.com/music.mp3"] * 10,
        },
    ]:
        result = await seedance_create_task(
            SeedanceCreateTaskInput.model_validate({"model": MODEL, "dry_run": True, **media}),
            flash_ctx,
        )
        assert isinstance(result, SeedanceCreateTaskOutput)
        assert result.preview is not None
    assert captured_creates == []


async def test_custom_flash_endpoint_keeps_flash_limits(
    flash_ctx: FakeContext,
    captured_creates: list[SeedanceCreateProviderRequest],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "SEEDANCE_MODEL_BINDINGS",
        '[{"model_id":"dreamina-seedance-2-5-260628","family":"seedance_2_5"},'
        '{"model_id":"ep-flash","family":"seedance_2_5_flash"}]',
    )
    get_settings.cache_clear()
    refresh_capability_registry()
    caps = get_capability_registry().resolve_video_model("ep-flash")
    assert caps.supported_resolutions == ("480p", "720p")
    assert caps.supports_draft is False
    with pytest.raises(ValueError, match="resolution '4k' is not supported"):
        await seedance_create_task(
            SeedanceCreateTaskInput(model="ep-flash", prompt="A tea ad", resolution="4k"),
            flash_ctx,
        )
    assert captured_creates == []
