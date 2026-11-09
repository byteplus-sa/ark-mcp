from __future__ import annotations

from collections.abc import Iterator

import pytest

from ark_mcp.config.env import Settings, get_settings
from ark_mcp.config.model_capabilities import (
    CapabilityRegistry,
    refresh_capability_registry,
)
from ark_mcp.tools._capability_checks import check_image_request
from ark_mcp.tools._seedream_shared import build_seedream_request
from ark_mcp.tools.seedream_generate_image import SeedreamGenerateInput

MODEL = "dola-seedream-5-5-261028"
PRO_MODEL = "dola-seedream-5-0-pro-260628"


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> Iterator[CapabilityRegistry]:
    monkeypatch.setenv("SEEDREAM_DEFAULT_MODEL", MODEL)
    monkeypatch.setenv(
        "SEEDREAM_MODEL_BINDINGS",
        f'[{{"model_id":"{MODEL}","family":"seedream_5_5"}},'
        f'{{"model_id":"{PRO_MODEL}","family":"pro"}}]',
    )
    get_settings.cache_clear()
    yield refresh_capability_registry()
    get_settings.cache_clear()
    refresh_capability_registry()


def test_seedream55_is_default_without_configuration() -> None:
    settings = Settings(_env_file=None)
    assert settings.seedream_default_model == MODEL
    assert [(b.model_id, b.family.value) for b in settings.seedream_model_bindings] == [
        (MODEL, "seedream_5_5")
    ]


@pytest.mark.parametrize(("model", "family"), [(MODEL, "seedream_5_5"), (PRO_MODEL, "pro")])
def test_recognized_seedream_ids_infer_their_own_family(model: str, family: str) -> None:
    settings = Settings(_env_file=None, SEEDREAM_DEFAULT_MODEL=model)
    assert settings.seedream_model_bindings[0].family.value == family


def test_seedream55_custom_endpoint_binding() -> None:
    settings = Settings(
        _env_file=None,
        SEEDREAM_DEFAULT_MODEL="ep-image-test",
        SEEDREAM_MODEL_FAMILY="seedream_5_5",
    )
    assert settings.seedream_model_bindings[0].model_id == "ep-image-test"
    assert settings.seedream_model_bindings[0].family.value == "seedream_5_5"


def test_explicit_bindings_still_require_the_default_to_be_present() -> None:
    with pytest.raises(ValueError, match="missing from its bindings"):
        Settings(
            _env_file=None,
            SEEDREAM_MODEL_BINDINGS=[{"model_id": PRO_MODEL, "family": "pro"}],
        )


def test_seedream55_capabilities_are_distinct_from_pro(registry: CapabilityRegistry) -> None:
    caps = registry.resolve_image_model(None)
    assert caps.model_id == MODEL
    assert caps.family.value == "seedream_5_5"
    assert caps.max_references == 10
    assert caps.supported_output_formats == ("png", "jpeg")
    assert not caps.supports_batch
    assert not caps.supports_streaming
    assert caps.supports_transparent_background
    assert caps.supports_transparent_generation
    assert caps.resolve_prompt_optimization(None) is None
    assert not registry.resolve_image_model(PRO_MODEL).supports_transparent_generation


@pytest.mark.parametrize("count", [0, 1, 10])
def test_seedream55_transparency_accepts_optional_non_png_references(
    registry: CapabilityRegistry, count: int
) -> None:
    request = SeedreamGenerateInput(
        prompt="Embroidered heart sticker",
        background="transparent",
        images=[{"kind": "base64", "data": "YWJj", "mime_type": "image/jpeg"}] * count,
    )
    assert check_image_request(request, registry.resolve_image_model(MODEL)) == []


@pytest.mark.parametrize(
    ("arguments", "violation"),
    [
        ({"output_format": "jpeg", "background": "transparent"}, "requires output_format"),
        ({"max_images": 2}, "batch generation"),
        ({"prompt_optimization": "fast"}, "prompt_optimization is not supported"),
        (
            {"images": [{"kind": "url", "url": "https://example.com/image.png"}] * 11},
            "maximum of 10",
        ),
    ],
)
def test_seedream55_rejects_invalid_combinations(
    registry: CapabilityRegistry, arguments: dict[str, object], violation: str
) -> None:
    request = SeedreamGenerateInput(prompt="test", **arguments)
    assert any(
        violation in item
        for item in check_image_request(request, registry.resolve_image_model(MODEL))
    )


@pytest.mark.parametrize(
    "images", [None, [], [{"kind": "url", "url": "https://example.com/a.png"}] * 2]
)
def test_pro_still_requires_one_transparent_input(
    registry: CapabilityRegistry, images: list[dict[str, str]] | None
) -> None:
    request = SeedreamGenerateInput(prompt="test", background="transparent", images=images)
    assert any(
        "exactly one input" in item
        for item in check_image_request(request, registry.resolve_image_model(PRO_MODEL))
    )


@pytest.mark.parametrize("output_format", [None, "png", "jpeg"])
def test_seedream55_request_uses_png_default_and_preserves_explicit_format(
    registry: CapabilityRegistry, output_format: str | None
) -> None:
    request = SeedreamGenerateInput(
        prompt="Embroidered heart sticker", output_format=output_format, size="2K"
    )
    body = build_seedream_request(
        request, registry.resolve_image_model(MODEL), None, prompt=request.prompt, seed=None
    ).model_dump(exclude_none=True)
    assert body == {
        "model": MODEL,
        "prompt": "Embroidered heart sticker",
        "size": "2K",
        "output_format": output_format or "png",
        "stream": False,
    }


def test_seedream55_request_matches_supplied_transparent_example(
    registry: CapabilityRegistry,
) -> None:
    request = SeedreamGenerateInput(
        prompt="Embroidered heart sticker",
        size="2K",
        output_format="png",
        response_format="url",
        background="transparent",
        watermark=True,
    )
    body = build_seedream_request(
        request, registry.resolve_image_model(MODEL), None, prompt=request.prompt, seed=None
    ).model_dump(exclude_none=True)
    assert body == {
        "model": MODEL,
        "prompt": "Embroidered heart sticker",
        "size": "2K",
        "response_format": "url",
        "output_format": "png",
        "background": "transparent",
        "watermark": True,
        "stream": False,
    }
