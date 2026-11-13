"""Unit tests for Seedance reference input auto-coercion."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ark_mcp.tools._seedance_shared import (
    SeedanceAudioInput,
    SeedanceVideoInput,
)
from ark_mcp.tools.seedance_create_task import SeedanceCreateTaskInput


class TestSeedanceImageCoercion:
    """Image reference accepts a plain URL string or {"url": ...} shorthand."""

    def test_plain_string_coerced_to_reference_image(self) -> None:
        inp = SeedanceCreateTaskInput(
            prompt="a cat",
            images=["https://example.com/cat.png"],
        )
        img = inp.images[0]
        assert img.kind == "url"
        assert img.url == "https://example.com/cat.png"
        assert img.role == "reference_image"

    def test_url_only_dict_coerced(self) -> None:
        inp = SeedanceCreateTaskInput(
            prompt="a cat",
            images=[{"url": "https://example.com/cat.png"}],
        )
        img = inp.images[0]
        assert img.kind == "url"
        assert img.url == "https://example.com/cat.png"
        assert img.role == "reference_image"

    def test_full_dict_preserves_explicit_role(self) -> None:
        inp = SeedanceCreateTaskInput(
            prompt="a cat",
            images=[{"kind": "url", "url": "https://example.com/start.png", "role": "first_frame"}],
        )
        img = inp.images[0]
        assert img.kind == "url"
        assert img.role == "first_frame"

    def test_full_dict_without_role_keeps_none(self) -> None:
        inp = SeedanceCreateTaskInput(
            prompt="a cat",
            images=[{"kind": "url", "url": "https://example.com/cat.png"}],
        )
        img = inp.images[0]
        assert img.kind == "url"
        assert img.role is None

    def test_base64_dict_unaffected(self) -> None:
        inp = SeedanceCreateTaskInput(
            prompt="a cat",
            images=[{"kind": "base64", "data": "aGVsbG8=", "mime_type": "image/png"}],
        )
        img = inp.images[0]
        assert img.kind == "base64"
        assert img.role is None

    def test_malformed_dict_raises_tailored_error(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            SeedanceCreateTaskInput(prompt="a cat", images=[{"mime_type": "image/png"}])
        assert "Expected each image reference" in str(exc_info.value)

    def test_data_only_dict_raises_tailored_error(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            SeedanceCreateTaskInput(
                prompt="a cat",
                images=[{"data": "aGVsbG8=", "mime_type": "image/png"}],
            )
        assert "Expected each image reference" in str(exc_info.value)


class TestSeedanceAudioCoercion:
    """Audio reference accepts a plain URL string or {"url": ...} shorthand."""

    def test_plain_string_coerced(self) -> None:
        inp = SeedanceCreateTaskInput(
            prompt="dance",
            videos=[SeedanceVideoInput(url="https://example.com/v.mp4")],
            audios=["https://example.com/song.wav"],
        )
        aud = inp.audios[0]
        assert aud.kind == "url"
        assert aud.url == "https://example.com/song.wav"
        assert aud.role == "reference_audio"

    def test_url_only_dict_coerced(self) -> None:
        aud = SeedanceAudioInput.model_validate({"url": "https://example.com/song.wav"})
        assert aud.kind == "url"
        assert aud.url == "https://example.com/song.wav"
        assert aud.role == "reference_audio"

    def test_malformed_dict_raises_tailored_error(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            SeedanceAudioInput.model_validate({"mime_type": "audio/mpeg"})
        assert "Expected each audio reference" in str(exc_info.value)


class TestSeedanceVideoCoercion:
    """Video reference accepts a plain URL string."""

    def test_plain_string_coerced(self) -> None:
        vid = SeedanceVideoInput.model_validate("https://example.com/v.mp4")
        assert vid.kind == "url"
        assert vid.url == "https://example.com/v.mp4"
        assert vid.role == "reference_video"

    def test_dict_unaffected(self) -> None:
        vid = SeedanceVideoInput.model_validate({"url": "https://example.com/v.mp4"})
        assert vid.url == "https://example.com/v.mp4"
        assert vid.role == "reference_video"


class TestSeedance25Coercion:
    """Seedance 2.5 inherits the same coercion via shared models."""

    def test_plain_string_image_coerced(self) -> None:
        inp = SeedanceCreateTaskInput(
            prompt="a cat",
            images=["https://example.com/cat.png"],
        )
        assert inp.images[0].kind == "url"
        assert inp.images[0].role == "reference_image"
