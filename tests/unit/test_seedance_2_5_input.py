"""Unit tests for Seedance 2.5 input model validators."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ark_mcp.tools._seedance_shared import (
    SeedanceAudioInput,
    SeedanceImageInput,
    SeedanceVideoInput,
)
from ark_mcp.tools.seedance_2_5_create_task import Seedance25CreateTaskInput
from ark_mcp.tools.seedance_2_5_create_task_variations import (
    Seedance25VariationsInput,
)


class TestSeedance25CreateTaskInput:
    """Tests for Seedance25CreateTaskInput validators."""

    def test_text_only_accepted(self) -> None:
        inp = Seedance25CreateTaskInput(prompt="a cat walking")
        assert inp.prompt == "a cat walking"

    def test_duration_30_accepted(self) -> None:
        inp = Seedance25CreateTaskInput(prompt="test", duration=30)
        assert inp.duration == 30

    def test_duration_31_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Seedance25CreateTaskInput(prompt="test", duration=31)

    def test_duration_auto_accepted(self) -> None:
        inp = Seedance25CreateTaskInput(prompt="test", duration=-1)
        assert inp.duration == -1

    def test_resolution_480p_accepted(self) -> None:
        inp = Seedance25CreateTaskInput(prompt="test", resolution="480p")
        assert inp.resolution == "480p"

    def test_resolution_720p_accepted(self) -> None:
        inp = Seedance25CreateTaskInput(prompt="test", resolution="720p")
        assert inp.resolution == "720p"

    def test_resolution_1080p_accepted(self) -> None:
        inp = Seedance25CreateTaskInput(prompt="test", resolution="1080p")
        assert inp.resolution == "1080p"

    def test_resolution_4k_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Seedance25CreateTaskInput(prompt="test", resolution="4k")

    def test_30_images_accepted(self) -> None:
        images = [
            SeedanceImageInput(kind="url", url=f"https://example.com/img{i}.jpg") for i in range(30)
        ]
        inp = Seedance25CreateTaskInput(prompt="test", images=images)
        assert len(inp.images) == 30

    def test_31_images_rejected(self) -> None:
        images = [
            SeedanceImageInput(kind="url", url=f"https://example.com/img{i}.jpg") for i in range(31)
        ]
        with pytest.raises(ValidationError):
            Seedance25CreateTaskInput(prompt="test", images=images)

    def test_10_videos_accepted(self) -> None:
        videos = [SeedanceVideoInput(url=f"https://example.com/vid{i}.mp4") for i in range(10)]
        inp = Seedance25CreateTaskInput(prompt="test", videos=videos)
        assert len(inp.videos) == 10

    def test_11_videos_rejected(self) -> None:
        videos = [SeedanceVideoInput(url=f"https://example.com/vid{i}.mp4") for i in range(11)]
        with pytest.raises(ValidationError):
            Seedance25CreateTaskInput(prompt="test", videos=videos)

    def test_10_audios_accepted(self) -> None:
        videos = [SeedanceVideoInput(url="https://example.com/vid.mp4")]
        audios = [
            SeedanceAudioInput(kind="url", url=f"https://example.com/aud{i}.mp3") for i in range(10)
        ]
        inp = Seedance25CreateTaskInput(prompt="test", videos=videos, audios=audios)
        assert len(inp.audios) == 10

    def test_11_audios_rejected(self) -> None:
        videos = [SeedanceVideoInput(url="https://example.com/vid.mp4")]
        audios = [
            SeedanceAudioInput(kind="url", url=f"https://example.com/aud{i}.mp3") for i in range(11)
        ]
        with pytest.raises(ValidationError):
            Seedance25CreateTaskInput(prompt="test", videos=videos, audios=audios)

    def test_audio_only_accepted(self) -> None:
        """Seedance 2.5 supports audio-only input (unique to 2.5)."""
        audios = [SeedanceAudioInput(kind="url", url="https://example.com/aud.mp3")]
        inp = Seedance25CreateTaskInput(audios=audios)
        assert inp.audios is not None
        assert len(inp.audios) == 1

    def test_audio_only_with_prompt_accepted(self) -> None:
        """Audio + prompt is also valid."""
        audios = [SeedanceAudioInput(kind="url", url="https://example.com/aud.mp3")]
        inp = Seedance25CreateTaskInput(prompt="dancing to the beat", audios=audios)
        assert inp.prompt == "dancing to the beat"
        assert len(inp.audios or []) == 1

    def test_no_prompt_no_media_rejected(self) -> None:
        with pytest.raises(ValidationError, match="At least one"):
            Seedance25CreateTaskInput()

    def test_omni_reference_task_type_accepted(self) -> None:
        inp = Seedance25CreateTaskInput(
            prompt="edit this video",
            omni_reference_task_type="edit_video",
        )
        assert inp.omni_reference_task_type == "edit_video"

    def test_omni_reference_task_type_none_default(self) -> None:
        inp = Seedance25CreateTaskInput(prompt="test")
        assert inp.omni_reference_task_type is None

    def test_ratio_stripped_for_extend_video(self) -> None:
        """Ratio is stripped for extend_video to prevent InvalidParameter.TaskTypeConstraint."""
        inp = Seedance25CreateTaskInput(
            prompt="extend this video",
            omni_reference_task_type="extend_video",
            ratio="16:9",
        )
        assert inp.ratio is None

    def test_ratio_preserved_for_edit_video(self) -> None:
        """Ratio is not stripped for edit_video (only extension auto-locks ratio)."""
        inp = Seedance25CreateTaskInput(
            prompt="edit this video",
            omni_reference_task_type="edit_video",
            ratio="16:9",
        )
        assert inp.ratio == "16:9"

    def test_ratio_preserved_for_auto_task_type(self) -> None:
        """Ratio is not stripped when omni_reference_task_type is omitted (auto)."""
        inp = Seedance25CreateTaskInput(prompt="generate a video", ratio="16:9")
        assert inp.ratio == "16:9"

    def test_ratio_none_for_extend_video_stays_none(self) -> None:
        """No stripping when ratio is already None."""
        inp = Seedance25CreateTaskInput(
            prompt="extend this video",
            omni_reference_task_type="extend_video",
        )
        assert inp.ratio is None


class TestSeedance25VariationsInput:
    """Tests for Seedance25VariationsInput validators."""

    def test_prompt_required_when_no_variation_prompts(self) -> None:
        """When media is provided but neither prompt nor variation_prompts, raises."""
        images = [SeedanceImageInput(kind="url", url="https://example.com/img.jpg")]
        with pytest.raises(ValidationError, match="Either prompt or variation_prompts"):
            Seedance25VariationsInput(variations=2, images=images)

    def test_variation_prompts_length_must_match(self) -> None:
        with pytest.raises(ValidationError, match="exactly 3"):
            Seedance25VariationsInput(
                variations=3,
                variation_prompts=["a", "b"],
            )

    def test_too_many_variations_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Seedance25VariationsInput(variations=6, prompt="test")

    def test_valid_variations(self) -> None:
        inp = Seedance25VariationsInput(
            variations=3,
            variation_prompts=["a cat", "a dog", "a bird"],
        )
        assert inp.variations == 3
        assert len(inp.variation_prompts) == 3

    def test_inherits_2_5_duration_limit(self) -> None:
        inp = Seedance25VariationsInput(prompt="test", variations=1, duration=30)
        assert inp.duration == 30

    def test_inherits_2_5_duration_rejection(self) -> None:
        with pytest.raises(ValidationError):
            Seedance25VariationsInput(prompt="test", variations=1, duration=31)

    def test_inherits_ratio_stripping_for_extend_video(self) -> None:
        """Variations input inherits ratio stripping from base model."""
        inp = Seedance25VariationsInput(
            variations=2,
            variation_prompts=["extend clip a", "extend clip b"],
            omni_reference_task_type="extend_video",
            ratio="9:16",
        )
        assert inp.ratio is None


class TestSeedance25DraftModeInput:
    """Draft mode validators shared by the Seedance 2.5 and Premium create tools."""

    def test_draft_defaults_resolution_to_480p(self) -> None:
        inp = Seedance25CreateTaskInput(prompt="test", draft=True)
        assert inp.resolution == "480p"

    def test_draft_explicit_480p_accepted(self) -> None:
        inp = Seedance25CreateTaskInput(prompt="test", draft=True, resolution="480p")
        assert inp.resolution == "480p"

    def test_draft_other_resolution_rejected(self) -> None:
        with pytest.raises(ValidationError, match="only support resolution '480p'"):
            Seedance25CreateTaskInput(prompt="test", draft=True, resolution="720p")

    def test_draft_false_keeps_resolution_unset(self) -> None:
        inp = Seedance25CreateTaskInput(prompt="test", draft=False)
        assert inp.resolution is None

    def test_draft_task_id_alone_accepted(self) -> None:
        inp = Seedance25CreateTaskInput(draft_task_id="cgt-draft")
        assert inp.draft_task_id == "cgt-draft"
        assert inp.resolution == "1080p"

    def test_draft_task_id_1080p_accepted(self) -> None:
        inp = Seedance25CreateTaskInput(draft_task_id="cgt-draft", resolution="1080p")
        assert inp.resolution == "1080p"

    def test_draft_task_id_other_resolution_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Draft task"):
            Seedance25CreateTaskInput(draft_task_id="cgt-draft", resolution="720p")

    def test_draft_and_draft_task_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="mutually exclusive"):
            Seedance25CreateTaskInput(draft=True, draft_task_id="cgt-draft")

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("prompt", "repeat the prompt"),
            ("images", ["https://example.com/a.png"]),
            ("videos", ["https://example.com/a.mp4"]),
            ("audios", ["https://example.com/a.mp3"]),
            ("duration", 5),
            ("ratio", "16:9"),
            ("generate_audio", True),
            ("omni_reference_task_type", "auto"),
        ],
    )
    def test_draft_task_id_rejects_reused_fields(self, field: str, value: object) -> None:
        with pytest.raises(ValidationError, match=f"{field} must be omitted"):
            Seedance25CreateTaskInput.model_validate({"draft_task_id": "cgt-draft", field: value})

    def test_draft_task_id_allows_output_settings(self) -> None:
        inp = Seedance25CreateTaskInput(
            draft_task_id="cgt-draft",
            watermark=True,
            return_last_frame=True,
            priority=3,
            execution_expires_after=7200,
            safety_identifier="user-1",
        )
        assert inp.watermark is True

    def test_draft_task_id_rejects_4k(self) -> None:
        with pytest.raises(ValidationError):
            Seedance25CreateTaskInput.model_validate(
                {"draft_task_id": "cgt-draft", "resolution": "4k"}
            )

    def test_premium_draft_task_id_defaults_to_4k(self) -> None:
        from ark_mcp.tools.seedance_2_5_premium_create_task import (
            Seedance25PremiumCreateTaskInput,
        )

        inp = Seedance25PremiumCreateTaskInput(draft_task_id="cgt-draft")
        assert inp.resolution == "4k"

    def test_premium_draft_task_id_explicit_4k_accepted(self) -> None:
        from ark_mcp.tools.seedance_2_5_premium_create_task import (
            Seedance25PremiumCreateTaskInput,
        )

        inp = Seedance25PremiumCreateTaskInput(draft_task_id="cgt-draft", resolution="4k")
        assert inp.resolution == "4k"

    def test_premium_draft_task_id_rejects_1080p(self) -> None:
        from ark_mcp.tools.seedance_2_5_premium_create_task import (
            Seedance25PremiumCreateTaskInput,
        )

        with pytest.raises(ValidationError, match="set it to '4k'"):
            Seedance25PremiumCreateTaskInput(draft_task_id="cgt-draft", resolution="1080p")

    def test_premium_draft_still_480p(self) -> None:
        from ark_mcp.tools.seedance_2_5_premium_create_task import (
            Seedance25PremiumCreateTaskInput,
        )

        with pytest.raises(ValidationError, match="only support resolution '480p'"):
            Seedance25PremiumCreateTaskInput(prompt="test", draft=True, resolution="4k")

    def test_variations_draft_defaults_480p(self) -> None:
        inp = Seedance25VariationsInput(prompt="test", variations=2, draft=True)
        assert inp.resolution == "480p"

    def test_variations_reject_draft_task_id(self) -> None:
        with pytest.raises(ValidationError):
            Seedance25VariationsInput.model_validate(
                {"prompt": "test", "draft_task_id": "cgt-draft"}
            )

    def test_variations_schema_hides_draft_task_id(self) -> None:
        props = Seedance25VariationsInput.model_json_schema()["properties"]
        assert "draft" in props
        assert "draft_task_id" not in props
