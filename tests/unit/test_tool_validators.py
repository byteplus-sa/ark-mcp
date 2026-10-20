"""Unit tests for tool input validators."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ark_mcp.domain.media import AudioReference, MediaSource, MediaSourceKind
from ark_mcp.tools._seedream_shared import EditCoordinate, edit_prompt
from ark_mcp.tools.gen_task_cancel_or_delete import (
    GenTaskCancelOrDeleteInput,
)
from ark_mcp.tools.seed_audio_generate import SeedAudioGenerateInput
from ark_mcp.tools.seedance_create_task import SeedanceCreateTaskInput
from ark_mcp.tools.seedream_generate_image import SeedreamGenerateInput


class TestSeedAudioGenerateInput:
    """Tests for Seed Audio tool input validation."""

    def test_text_only_valid(self) -> None:
        inp = SeedAudioGenerateInput(text_prompt="Hello world")
        assert inp.text_prompt == "Hello world"
        assert inp.audio_references == []
        assert inp.image_reference is None

    def test_empty_prompt_raises(self) -> None:
        with pytest.raises(ValidationError):
            SeedAudioGenerateInput(text_prompt="")

    def test_prompt_too_long_raises(self) -> None:
        with pytest.raises(ValidationError):
            SeedAudioGenerateInput(text_prompt="x" * 3001)

    def test_too_many_audio_references_raises(self) -> None:
        refs = [AudioReference(kind="speaker", speaker_id=f"v{i}") for i in range(4)]
        with pytest.raises(ValidationError):
            SeedAudioGenerateInput(text_prompt="test", audio_references=refs)

    def test_audio_and_image_mixing_raises(self) -> None:
        with pytest.raises(ValidationError, match="mutually exclusive"):
            SeedAudioGenerateInput(
                text_prompt="test",
                audio_references=[AudioReference(kind="speaker", speaker_id="v1")],
                image_reference=MediaSource(
                    kind=MediaSourceKind.url, url="https://example.com/img.png"
                ),
            )


class TestSeedanceCreateTaskInput:
    """Tests for Seedance create task input validation."""

    def test_with_images_valid(self) -> None:
        from ark_mcp.tools.seedance_create_task import SeedanceImageInput

        inp = SeedanceCreateTaskInput(
            prompt="A cat",
            images=[SeedanceImageInput(kind="url", url="https://example.com/cat.png")],
        )
        assert inp.prompt == "A cat"
        assert len(inp.images or []) == 1

    def test_with_videos_valid(self) -> None:
        from ark_mcp.tools.seedance_create_task import SeedanceVideoInput

        inp = SeedanceCreateTaskInput(
            prompt="A dog",
            videos=[SeedanceVideoInput(url="https://example.com/dog.mp4")],
        )
        assert len(inp.videos or []) == 1

    def test_blocked_ip_video_url_raises_sanitized_error(self) -> None:
        from ark_mcp.tools.seedance_create_task import SeedanceVideoInput

        with pytest.raises(ValidationError) as exc_info:
            SeedanceVideoInput(url="https://10.0.0.1/dog.mp4")
        message = str(exc_info.value)
        assert "Invalid media URL." in message
        assert "resolves to blocked" not in message

    def test_text_only_valid(self) -> None:
        inp = SeedanceCreateTaskInput(prompt="Just text")
        assert inp.prompt == "Just text"
        assert inp.images is None
        assert inp.videos is None
        assert inp.audios is None

    def test_empty_request_raises(self) -> None:
        with pytest.raises(ValidationError, match="At least one of prompt"):
            SeedanceCreateTaskInput()

    def test_prompt_with_video_and_audio_valid(self) -> None:
        from ark_mcp.tools.seedance_create_task import (
            SeedanceAudioInput,
            SeedanceVideoInput,
        )

        inp = SeedanceCreateTaskInput(
            prompt="A dancer moving to music",
            videos=[SeedanceVideoInput(url="https://example.com/dance.mp4")],
            audios=[SeedanceAudioInput(kind="url", url="https://example.com/song.wav")],
        )
        assert inp.prompt == "A dancer moving to music"
        assert inp.images is None
        assert len(inp.videos or []) == 1
        assert len(inp.audios or []) == 1

    def test_audio_input_rejects_image_mime(self) -> None:
        from ark_mcp.tools.seedance_create_task import SeedanceAudioInput

        with pytest.raises(ValidationError, match="Audio MIME type"):
            SeedanceAudioInput(kind="base64", data="aGVsbG8=", mime_type="image/png")

    def test_empty_prompt_raises(self) -> None:
        from ark_mcp.tools.seedance_create_task import SeedanceVideoInput

        with pytest.raises(ValidationError):
            SeedanceCreateTaskInput(
                prompt="",
                videos=[SeedanceVideoInput(url="https://example.com/v.mp4")],
            )

    def test_prompt_at_max_length_valid(self) -> None:
        from ark_mcp.tools.seedance_create_task import SeedanceVideoInput

        inp = SeedanceCreateTaskInput(
            prompt="x" * 32000,
            videos=[SeedanceVideoInput(url="https://example.com/v.mp4")],
        )
        assert len(inp.prompt or "") == 32000

    def test_prompt_too_long_raises(self) -> None:
        from ark_mcp.tools.seedance_create_task import SeedanceVideoInput

        with pytest.raises(ValidationError):
            SeedanceCreateTaskInput(
                prompt="x" * 32001,
                videos=[SeedanceVideoInput(url="https://example.com/v.mp4")],
            )

    def test_ratio_preserved_for_auto_task_type(self) -> None:
        """Ratio is not stripped when omni_reference_task_type is omitted."""
        from ark_mcp.tools.seedance_create_task import SeedanceVideoInput

        inp = SeedanceCreateTaskInput(
            prompt="generate a video",
            videos=[SeedanceVideoInput(url="https://example.com/v.mp4")],
            ratio="16:9",
        )
        assert inp.ratio == "16:9"

    def test_outer_bounds_still_enforced_statically(self) -> None:
        with pytest.raises(ValidationError):
            SeedanceCreateTaskInput(prompt="x", duration=31)
        with pytest.raises(ValidationError):
            SeedanceCreateTaskInput(
                prompt="x", images=[f"https://example.com/{i}.png" for i in range(31)]
            )


class TestGenTaskCancelOrDeleteInput:
    """Tests for Seedance cancel/delete input validation."""

    def test_cancel_queued_valid(self) -> None:
        inp = GenTaskCancelOrDeleteInput(
            task_id="task-123", mode="cancel", expected_status="queued"
        )
        assert inp.mode == "cancel"

    def test_delete_succeeded_valid(self) -> None:
        inp = GenTaskCancelOrDeleteInput(
            task_id="task-123", mode="delete", expected_status="succeeded"
        )
        assert inp.mode == "delete"

    def test_cancel_succeeded_raises(self) -> None:
        with pytest.raises(ValidationError, match="Cancel mode requires"):
            GenTaskCancelOrDeleteInput(
                task_id="task-123", mode="cancel", expected_status="succeeded"
            )

    def test_delete_queued_raises(self) -> None:
        with pytest.raises(ValidationError, match="Delete mode requires"):
            GenTaskCancelOrDeleteInput(task_id="task-123", mode="delete", expected_status="queued")


class TestSeedanceVariationsInput:
    """Tests for Seedance variations input prompt-length validation."""

    def test_variation_prompt_at_max_length_valid(self) -> None:
        from ark_mcp.tools.seedance_create_task import SeedanceVideoInput

        inp = SeedanceCreateTaskInput(
            variation_prompts=["x" * 32000],
            variations=1,
            videos=[SeedanceVideoInput(url="https://example.com/v.mp4")],
        )
        assert len(inp.variation_prompts[0]) == 32000

    def test_variation_prompt_too_long_raises(self) -> None:
        from ark_mcp.tools.seedance_create_task import SeedanceVideoInput

        with pytest.raises(ValidationError):
            SeedanceCreateTaskInput(
                variation_prompts=["x" * 32001],
                variations=1,
                videos=[SeedanceVideoInput(url="https://example.com/v.mp4")],
            )

    def test_prompt_at_max_length_valid(self) -> None:
        inp = SeedanceCreateTaskInput(prompt="x" * 32000, variations=1)
        assert len(inp.prompt or "") == 32000

    def test_prompt_too_long_raises(self) -> None:
        with pytest.raises(ValidationError):
            SeedanceCreateTaskInput(prompt="x" * 32001, variations=1)


class TestSeedance25CreateTaskInput:
    """Tests for Seedance 2.5 create task prompt-length validation."""

    def test_prompt_at_max_length_valid(self) -> None:
        inp = SeedanceCreateTaskInput(prompt="x" * 32000)
        assert len(inp.prompt or "") == 32000

    def test_prompt_too_long_raises(self) -> None:
        with pytest.raises(ValidationError):
            SeedanceCreateTaskInput(prompt="x" * 32001)


class TestSeedance25VariationsInput:
    """Tests for Seedance 2.5 variations prompt-length validation."""

    def test_prompt_at_max_length_valid(self) -> None:
        inp = SeedanceCreateTaskInput(prompt="x" * 32000, variations=1)
        assert len(inp.prompt or "") == 32000

    def test_prompt_too_long_raises(self) -> None:
        with pytest.raises(ValidationError):
            SeedanceCreateTaskInput(prompt="x" * 32001, variations=1)

    def test_variation_prompt_at_max_length_valid(self) -> None:
        inp = SeedanceCreateTaskInput(variation_prompts=["x" * 32000], variations=1)
        assert len(inp.variation_prompts[0]) == 32000

    def test_variation_prompt_too_long_raises(self) -> None:
        with pytest.raises(ValidationError):
            SeedanceCreateTaskInput(variation_prompts=["x" * 32001], variations=1)


class TestSeedreamPromptLength:
    """Tests for Seedream prompt length validation (4,000 characters)."""

    def test_generate_prompt_at_max_length_valid(self) -> None:
        inp = SeedreamGenerateInput(prompt="x" * 4000)
        assert len(inp.prompt) == 4000

    def test_generate_prompt_too_long_raises(self) -> None:
        with pytest.raises(ValidationError):
            SeedreamGenerateInput(prompt="x" * 4001)

    def test_variations_prompt_at_max_length_valid(self) -> None:
        inp = SeedreamGenerateInput(prompt="x" * 4000, variations=1)
        assert len(inp.prompt or "") == 4000

    def test_variations_prompt_too_long_raises(self) -> None:
        with pytest.raises(ValidationError):
            SeedreamGenerateInput(prompt="x" * 4001, variations=1)

    def test_variation_prompt_at_max_length_valid(self) -> None:
        inp = SeedreamGenerateInput(variation_prompts=["x" * 4000], variations=1)
        assert len(inp.variation_prompts[0]) == 4000

    def test_variation_prompt_too_long_raises(self) -> None:
        with pytest.raises(ValidationError):
            SeedreamGenerateInput(variation_prompts=["x" * 4001], variations=1)

    def _edit_image(self) -> MediaSource:
        return MediaSource(kind=MediaSourceKind.url, url="https://example.com/img.png")

    def test_edit_prompt_at_max_length_with_markup_valid(self) -> None:
        point = EditCoordinate(x=500, y=500)
        markup_len = len(edit_prompt("", point, None).rstrip())
        inp = SeedreamGenerateInput(
            prompt="x" * (4000 - markup_len - 1),
            images=[self._edit_image()],
            point=point,
        )
        assert len(inp.prompt) + markup_len + 1 == 4000

    def test_edit_prompt_with_markup_too_long_raises(self) -> None:
        point = EditCoordinate(x=500, y=500)
        markup_len = len(edit_prompt("", point, None).rstrip())
        with pytest.raises(ValidationError):
            SeedreamGenerateInput(
                prompt="x" * (4000 - markup_len),
                images=[self._edit_image()],
                point=point,
            )

    def test_edit_prompt_too_long_raises(self) -> None:
        with pytest.raises(ValidationError):
            SeedreamGenerateInput(
                prompt="x" * 4001,
                images=[self._edit_image()],
                point=EditCoordinate(x=500, y=500),
            )
