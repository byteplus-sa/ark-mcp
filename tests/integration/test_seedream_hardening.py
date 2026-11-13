from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from io import BytesIO
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from fastmcp import Client
from fastmcp.exceptions import ToolError
from PIL import Image

from ark_mcp.config.env import get_settings
from ark_mcp.config.model_capabilities import refresh_capability_registry
from ark_mcp.domain.errors import NormalizedProviderError, ProviderError
from ark_mcp.runtime import get_runtime
from ark_mcp.security.safe_downloader import DownloadedMedia
from ark_mcp.server import create_server
from ark_mcp.tools.seedream_generate_image import (
    SeedreamGenerateInput,
    SeedreamGenerateOutput,
    seedream_generate_image,
)
from tests.fixtures.fake_context import FakeContext
from tests.integration.test_seedream55 import _image_job

ARK_BASE = "https://ark.test.example.com/api/v3"


def image_bytes(*, transparent: bool = False, image_format: str = "PNG") -> bytes:
    output = BytesIO()
    mode = "RGBA" if transparent else "RGB"
    color = (255, 0, 0, 0) if transparent else (255, 0, 0)
    with Image.new(mode, (960, 960), color) as image:
        image.save(output, format=image_format)
    return output.getvalue()


@pytest.mark.parametrize("dry_run", [False, True])
async def test_base64_without_persistence_rejected_before_submission(
    test_env: None, dry_run: bool
) -> None:
    with respx.mock(assert_all_called=False) as router:
        async with Client(create_server(get_settings())) as client:
            with pytest.raises(ToolError, match=r"b64_json.*persist"):
                await _image_job(
                    client,
                    {
                        "prompt": "test",
                        "response_format": "b64_json",
                        "persist": False,
                        "dry_run": dry_run,
                    },
                )
    assert not router.calls


@pytest.mark.parametrize("variations", [1, 2])
async def test_provenance_parity_and_private_reference_exclusion(
    fake_ctx: FakeContext, variations: int
) -> None:
    prompt = "Make the subject red"
    with respx.mock:
        route = respx.post(f"{ARK_BASE}/images/generations").mock(
            return_value=httpx.Response(
                200,
                json={"data": [{"url": "https://example.com/output.png"}]},
                headers={"X-Request-Id": "request-trace"},
            )
        )
        result = await seedream_generate_image(
            SeedreamGenerateInput(
                prompt=prompt,
                seed=5001,
                variations=variations,
                images=[
                    {"kind": "url", "url": "https://example.com/private.png?signature=private"}
                ],
                bbox={"x1": 0, "y1": 0, "x2": 999, "y2": 999},
                safety_identifier="hashed-test-user",
                persist=False,
            ),
            fake_ctx,
        )
    assert isinstance(result, SeedreamGenerateOutput)
    for index, generation in enumerate(result.generations):
        assert generation.request_id == "request-trace"
        assert generation.seed == 5001 + index
        body = next(
            json.loads(call.request.content)
            for call in route.calls
            if json.loads(call.request.content)["seed"] == 5001 + index
        )
        assert generation.prompt_sha256 == hashlib.sha256(body["prompt"].encode()).hexdigest()
        assert generation.request_options["reference_count"] == 1
        assert generation.request_options["prompt_optimization"] == "standard"
        assert "private" not in json.dumps(generation.request_options)
        assert "hashed-test-user" not in json.dumps(generation.request_options)


@pytest.mark.parametrize("variations", [1, 2])
@pytest.mark.parametrize("items", [[], [{}], [{"b64_json": ""}]])
async def test_missing_outputs_report_failure_without_paid_retry(
    fake_ctx: FakeContext, variations: int, items: list[dict[str, Any]]
) -> None:
    with respx.mock:
        route = respx.post(f"{ARK_BASE}/images/generations").mock(
            return_value=httpx.Response(
                200, json={"data": items}, headers={"X-Request-Id": "empty-trace"}
            )
        )
        result = await seedream_generate_image(
            SeedreamGenerateInput(prompt="test", variations=variations, persist=False), fake_ctx
        )
    assert route.call_count == variations
    assert isinstance(result, SeedreamGenerateOutput)
    if variations == 1:
        assert result.artifacts == []
        assert result.item_errors[0].code == "NO_OUTPUT"
        assert result.generations[0].request_id == "empty-trace"
    else:
        assert result.summary is not None
        assert result.summary.succeeded == 0
        assert result.summary.failed == variations
        assert all(item.error.code == "NO_OUTPUT" for item in result.summary.variations)
        assert all(item.request_id == "empty-trace" for item in result.summary.variations)


@pytest.mark.parametrize("variations", [1, 2])
async def test_unexpected_base64_only_output_reports_undeliverable_without_retry(
    fake_ctx: FakeContext, variations: int
) -> None:
    with respx.mock:
        route = respx.post(f"{ARK_BASE}/images/generations").mock(
            return_value=httpx.Response(
                200, json={"data": [{"b64_json": base64.b64encode(image_bytes()).decode()}]}
            )
        )
        result = await seedream_generate_image(
            SeedreamGenerateInput(prompt="test", persist=False, variations=variations), fake_ctx
        )
    assert route.call_count == variations
    assert isinstance(result, SeedreamGenerateOutput)
    assert len(result.generations) == variations
    if variations == 1:
        assert result.artifacts == []
        assert result.item_errors[0].code == "NO_DELIVERABLE_OUTPUT"
    else:
        assert result.summary is not None
        assert result.summary.failed == variations
        assert all(item.error.code == "NO_DELIVERABLE_OUTPUT" for item in result.summary.variations)


@pytest.mark.parametrize("delivery", ["url", "inline-fallback"])
async def test_inspection_uses_persisted_url_or_inline_fallback_bytes(
    fake_ctx: FakeContext, delivery: str
) -> None:
    raw = image_bytes()
    store = get_runtime(fake_ctx).artifact_store
    if delivery == "url":
        item = {"url": "https://tos-ap-southeast.bytepluses.com/out.png"}
        storage_patch = patch(
            "ark_mcp.security.safe_downloader.SafeDownloader.download",
            AsyncMock(
                return_value=DownloadedMedia(
                    body=raw, content_type="image/png", final_url=item["url"]
                )
            ),
        )
    else:
        item = {"b64_json": base64.b64encode(raw).decode()}
        storage_patch = patch.object(
            store, "put_base64", AsyncMock(side_effect=OSError("private storage failure"))
        )
    with respx.mock, storage_patch:
        route = respx.post(f"{ARK_BASE}/images/generations").mock(
            return_value=httpx.Response(200, json={"data": [item]})
        )
        result = await seedream_generate_image(
            SeedreamGenerateInput(prompt="test", output_format="png"), fake_ctx
        )
    assert route.call_count == 1
    assert isinstance(result, SeedreamGenerateOutput)
    assert len(result.artifacts) == 1
    image = result.generations[0].images[0]
    assert image.inspection is not None
    assert image.inspection.status == "verified"
    assert image.inspection.width == 960
    if delivery == "inline-fallback":
        assert result.artifacts[0].id == "inline-fallback"
        assert result.artifacts[0].persistence_error is not None
        assert "private" not in result.model_dump_json()


@pytest.mark.parametrize("variations", [1, 2])
async def test_persisted_image_inspection_does_not_invent_provider_metadata(
    fake_ctx: FakeContext, variations: int
) -> None:
    raw = image_bytes(transparent=True)
    with respx.mock:
        respx.post(f"{ARK_BASE}/images/generations").mock(
            return_value=httpx.Response(
                200, json={"data": [{"b64_json": base64.b64encode(raw).decode()}]}
            )
        )
        result = await seedream_generate_image(
            SeedreamGenerateInput(
                prompt="red square",
                background="transparent",
                variations=variations,
                images=[{"kind": "url", "url": "https://example.com/transparent.png"}],
            ),
            fake_ctx,
        )
    assert isinstance(result, SeedreamGenerateOutput)
    for metadata in result.generations:
        image = metadata.images[0]
        assert image.size is None
        assert image.background is None
        assert image.output_format is None
        assert image.inspection is not None
        assert image.inspection.status == "verified"
        assert image.inspection.width == 960
        assert image.inspection.height == 960
        assert image.inspection.output_format == "png"
        assert image.inspection.has_transparency is True


async def test_inspection_mismatch_keeps_billed_artifact(fake_ctx: FakeContext) -> None:
    raw = image_bytes()
    with respx.mock:
        route = respx.post(f"{ARK_BASE}/images/generations").mock(
            return_value=httpx.Response(
                200, json={"data": [{"b64_json": base64.b64encode(raw).decode()}]}
            )
        )
        result = await seedream_generate_image(
            SeedreamGenerateInput(
                prompt="red square",
                background="transparent",
                images=[{"kind": "url", "url": "https://example.com/transparent.png"}],
            ),
            fake_ctx,
        )
    assert route.call_count == 1
    assert isinstance(result, SeedreamGenerateOutput)
    assert len(result.artifacts) == 1
    inspection = result.generations[0].images[0].inspection
    assert inspection is not None
    assert inspection.status == "mismatch"
    assert "requested_background_mismatch" in inspection.issues


async def test_variation_inspection_cancellation_preserves_persisted_outputs(
    fake_ctx: FakeContext,
) -> None:
    raw = image_bytes()

    async def cancelled_inspection(*args: Any) -> None:
        raise asyncio.CancelledError

    with (
        respx.mock,
        patch(
            "ark_mcp.tools._seedream_shared.attach_image_inspection",
            side_effect=cancelled_inspection,
        ),
    ):
        route = respx.post(f"{ARK_BASE}/images/generations").mock(
            return_value=httpx.Response(
                200,
                json={"data": [{"b64_json": base64.b64encode(raw).decode()}]},
                headers={"X-Request-Id": "cancelled-inspection"},
            )
        )
        result = await seedream_generate_image(
            SeedreamGenerateInput(prompt="test", variations=2, seed=42), fake_ctx
        )
    assert route.call_count == 2
    assert isinstance(result, SeedreamGenerateOutput)
    assert result.summary is not None
    assert result.summary.succeeded == 2
    assert result.summary.failed == 0
    for variation, metadata in zip(result.summary.variations, result.generations, strict=True):
        assert variation.artifact is not None
        assert variation.request_id == "cancelled-inspection"
        assert metadata.images[0].artifact_id == variation.artifact.id
        stored = await get_runtime(fake_ctx).artifact_store.get(variation.artifact.id)
        assert stored.data == raw


@pytest.mark.parametrize("seed", [None, -1])
async def test_single_random_seed_is_not_reported_as_recovered_seed(
    fake_ctx: FakeContext, seed: int | None
) -> None:
    with respx.mock:
        respx.post(f"{ARK_BASE}/images/generations").mock(
            return_value=httpx.Response(200, json={"data": [{"url": "https://example.com/o.png"}]})
        )
        result = await seedream_generate_image(
            SeedreamGenerateInput(prompt="test", seed=seed, persist=False), fake_ctx
        )
    assert isinstance(result, SeedreamGenerateOutput)
    assert result.generations[0].seed == seed


async def test_inspection_unavailability_preserves_artifact_and_redacts_exception(
    fake_ctx: FakeContext,
) -> None:
    raw = image_bytes()
    store = get_runtime(fake_ctx).artifact_store
    with (
        respx.mock,
        patch.object(
            store, "get", AsyncMock(side_effect=OSError("https://private.example/?secret=token"))
        ),
    ):
        route = respx.post(f"{ARK_BASE}/images/generations").mock(
            return_value=httpx.Response(
                200, json={"data": [{"b64_json": base64.b64encode(raw).decode()}]}
            )
        )
        result = await seedream_generate_image(SeedreamGenerateInput(prompt="test"), fake_ctx)
    assert route.call_count == 1
    assert isinstance(result, SeedreamGenerateOutput)
    assert len(result.artifacts) == 1
    inspection = result.generations[0].images[0].inspection
    assert inspection is not None
    assert inspection.status == "unavailable"
    assert "private" not in result.model_dump_json()


@pytest.mark.parametrize("variations", [1, 2])
async def test_storage_presign_error_during_inspection_retains_billed_output(
    fake_ctx: FakeContext, variations: int
) -> None:
    raw = image_bytes()
    failure = ProviderError(
        NormalizedProviderError(
            provider="tos",
            operation="presign_get",
            code="STORAGE_READ_FAILED",
            message="https://private.example/?token=secret",
            retryable=True,
        )
    )
    store = get_runtime(fake_ctx).artifact_store
    with respx.mock, patch.object(store, "get", AsyncMock(side_effect=failure)):
        route = respx.post(f"{ARK_BASE}/images/generations").mock(
            return_value=httpx.Response(
                200, json={"data": [{"b64_json": base64.b64encode(raw).decode()}]}
            )
        )
        result = await seedream_generate_image(
            SeedreamGenerateInput(prompt="test", variations=variations), fake_ctx
        )
    assert route.call_count == variations
    assert isinstance(result, SeedreamGenerateOutput)
    if variations == 1:
        assert len(result.artifacts) == 1
    else:
        assert result.summary is not None
        assert result.summary.succeeded == variations
        assert all(item.artifact is not None for item in result.summary.variations)
    for generation in result.generations:
        assert generation.images[0].inspection.status == "unavailable"
    assert "private" not in result.model_dump_json()


@pytest.mark.parametrize("inline_fallback", [False, True])
async def test_jpeg_mime_alias_does_not_create_false_inspection_mismatch(
    fake_ctx: FakeContext, inline_fallback: bool
) -> None:
    from ark_mcp.domain.artifacts import ArtifactRef, MediaType
    from ark_mcp.domain.models import SeedreamImageMetadata
    from ark_mcp.providers.modelark.schemas import SeedreamProviderRequest
    from ark_mcp.tools._seedream_shared import attach_image_inspection

    raw = image_bytes(image_format="JPEG")
    data = base64.b64encode(raw).decode()
    if inline_fallback:
        artifact = ArtifactRef(
            id="inline-fallback",
            uri="data:image/jpg;base64,",
            media_type=MediaType.IMAGE,
            mime_type="image/jpg",
            created_at="2026-11-10T00:00:00Z",
            fallback_data=data,
        )
    else:
        artifact = await get_runtime(fake_ctx).artifact_store.put_base64(
            data=data, media_type=MediaType.IMAGE, mime_type="image/jpg"
        )
    metadata = SeedreamImageMetadata(index=0)
    await attach_image_inspection(
        fake_ctx,
        artifact,
        metadata,
        SeedreamProviderRequest(model="test", prompt="test", output_format="jpeg"),
    )
    assert metadata.inspection is not None
    assert metadata.inspection.status == "verified"
    assert metadata.inspection.output_format == "jpeg"


async def test_inspection_detects_format_and_size_disagreement(fake_ctx: FakeContext) -> None:
    raw = image_bytes(image_format="JPEG")
    with respx.mock:
        route = respx.post(f"{ARK_BASE}/images/generations").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "b64_json": base64.b64encode(raw).decode(),
                            "size": "1024x1024",
                            "output_format": "png",
                        }
                    ]
                },
            )
        )
        result = await seedream_generate_image(
            SeedreamGenerateInput(prompt="test", size="1024x1024", output_format="png"), fake_ctx
        )
    assert route.call_count == 1
    assert isinstance(result, SeedreamGenerateOutput)
    image = result.generations[0].images[0]
    assert image.output_format == "png"
    assert image.size == "1024x1024"
    assert image.inspection is not None
    assert image.inspection.output_format == "jpeg"
    assert image.inspection.status == "mismatch"
    assert set(image.inspection.issues) == {
        "artifact_format_mismatch",
        "requested_format_mismatch",
        "provider_format_mismatch",
        "requested_size_mismatch",
        "provider_size_mismatch",
    }


async def test_dry_run_reports_fallback_price_source(test_env: None) -> None:
    async with Client(create_server(get_settings())) as client:
        result = await _image_job(client, {"prompt": "test", "dry_run": True})
    assert result["structured_content"]["preview"]["price_source"] == "builtin"


async def test_55_edit_jobs_accept_point_and_bbox_with_real_reference(
    test_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SEEDREAM_DEFAULT_MODEL")
    get_settings.cache_clear()
    refresh_capability_registry()
    raw = image_bytes()
    with respx.mock:
        route = respx.post(f"{ARK_BASE}/images/generations").mock(
            return_value=httpx.Response(
                200, json={"data": [{"b64_json": base64.b64encode(raw).decode()}]}
            )
        )
        async with Client(create_server(get_settings())) as client:
            result = await _image_job(
                client,
                {
                    "prompt": "Change red to blue",
                    "images": [
                        {
                            "kind": "base64",
                            "data": base64.b64encode(raw).decode(),
                            "mime_type": "image/png",
                        }
                    ],
                    "point": {"x": 500, "y": 500},
                    "bbox": {"x1": 100, "y1": 100, "x2": 900, "y2": 900},
                },
            )
    assert not result["is_error"]
    assert route.call_count == 1
    body = json.loads(route.calls[0].request.content)
    assert body["prompt"].startswith(
        "Image 1<bbox>100 100 900 900</bbox> Image 1<point>500 500</point>"
    )
    assert (
        result["structured_content"]["generations"][0]["images"][0]["inspection"]["status"]
        == "verified"
    )
