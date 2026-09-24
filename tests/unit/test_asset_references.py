"""Unit tests for ``asset://`` parsing and which media inputs accept it."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ark_mcp.domain.assets import AssetUriError, asset_uri, is_asset_uri, parse_asset_uri
from ark_mcp.domain.media import AudioReference, MediaSource, ReferenceImageInput
from ark_mcp.providers.modelark.seedance import SeedanceService
from ark_mcp.security.url_policy import UrlValidationError, validate_url
from ark_mcp.tools._asset_shared import collect_asset_ids, infer_asset_type
from ark_mcp.tools._seedance_shared import (
    SeedanceAudioInput,
    SeedanceImageInput,
    SeedanceVideoInput,
)

ASSET = "asset://asset-20260318035710-kctzf"


def test_parse_and_build_asset_uri() -> None:
    assert is_asset_uri(ASSET)
    assert is_asset_uri("ASSET://asset-1")
    assert not is_asset_uri("https://example.com/a.png")
    assert parse_asset_uri(ASSET) == "asset-20260318035710-kctzf"
    assert asset_uri("Asset-2026-abc") == "asset://Asset-2026-abc"


@pytest.mark.parametrize("bad", ["asset://", "asset://../etc", "asset://a b", "asset://x/y"])
def test_malformed_asset_uri_rejected(bad: str) -> None:
    with pytest.raises(AssetUriError):
        parse_asset_uri(bad)


def test_url_policy_still_rejects_asset_scheme() -> None:
    with pytest.raises(UrlValidationError):
        validate_url(ASSET)


def test_generic_media_source_rejects_asset_uri() -> None:
    with pytest.raises(ValidationError, match="not supported for this input"):
        MediaSource(kind="url", url=ASSET)


def test_reference_image_and_audio_reference_accept_asset_uri() -> None:
    assert ReferenceImageInput(kind="url", url=ASSET).url == ASSET
    assert AudioReference(kind="url", url=ASSET).url == ASSET
    with pytest.raises(ValidationError):
        ReferenceImageInput(kind="url", url="asset://bad id")


def test_reference_image_accepts_plain_media_source_instance() -> None:
    source = MediaSource(kind="url", url="https://example.com/a.png")
    assert ReferenceImageInput.model_validate(source).url == "https://example.com/a.png"


def test_seedance_inputs_accept_asset_uri_shorthand() -> None:
    image = SeedanceImageInput.model_validate(ASSET)
    assert image.url == ASSET
    assert image.role == "reference_image"
    assert SeedanceVideoInput.model_validate(ASSET).url == ASSET
    assert SeedanceAudioInput.model_validate(ASSET).url == ASSET


def test_seedance_content_passes_asset_uri_through() -> None:
    content = SeedanceService.build_content(
        prompt="The character in Image 1 waves",
        images=[SeedanceImageInput.model_validate(ASSET).model_dump()],
        videos=[SeedanceVideoInput.model_validate("asset://asset-video-1").model_dump()],
        audios=[SeedanceAudioInput.model_validate("asset://asset-audio-1").model_dump()],
    )
    urls = [
        (item.image_url or item.video_url or item.audio_url or {}).get("url")
        for item in content
        if item.type != "text"
    ]
    assert urls == [ASSET, "asset://asset-video-1", "asset://asset-audio-1"]


def test_collect_asset_ids_deduplicates() -> None:
    items = [{"url": ASSET}, {"url": "https://example.com/x.png"}, {"url": ASSET}]
    assert collect_asset_ids(items, None, [{"url": "asset://asset-2"}]) == [
        "asset-20260318035710-kctzf",
        "asset-2",
    ]


@pytest.mark.parametrize(
    ("url", "object_key", "expected"),
    [
        ("https://x.test/a/b.PNG?sig=1", None, "Image"),
        ("https://x.test/clip.mov", None, "Video"),
        ("https://x.test/voice.mp3", None, "Audio"),
        (None, "asset-test/image/6ad7ccaf", "Image"),
        ("https://x.test/no-extension", None, None),
    ],
)
def test_infer_asset_type(url: str | None, object_key: str | None, expected: str | None) -> None:
    assert infer_asset_type(url=url, object_key=object_key) == expected
