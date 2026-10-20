"""Unit tests for Seedance 2.5 input model validators."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ark_mcp.tools._seedance_shared import (
    SeedanceAudioInput,
    SeedanceImageInput,
    SeedanceVideoInput,
)
from ark_mcp.tools.seedance_create_task import SeedanceCreateTaskInput


class TestSeedance25CreateTaskInput:
    """Tests for SeedanceCreateTaskInput validators."""

    def test_text_only_accepted(self) -> None:
        inp = SeedanceCreateTaskInput(prompt="a cat walking")
        assert inp.prompt == "a cat walking"

    def test_duration_30_accepted(self) -> None:
        inp = SeedanceCreateTaskInput(prompt="test", duration=30)
        assert inp.duration == 30

    def test_duration_31_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SeedanceCreateTaskInput(prompt="test", duration=31)

    def test_duration_auto_accepted(self) -> None:
        inp = SeedanceCreateTaskInput(prompt="test", duration=-1)
        assert inp.duration == -1

    def test_resolution_480p_accepted(self) -> None:
        inp = SeedanceCreateTaskInput(prompt="test", resolution="480p")
        assert inp.resolution == "480p"

    def test_resolution_720p_accepted(self) -> None:
        inp = SeedanceCreateTaskInput(prompt="test", resolution="720p")
        assert inp.resolution == "720p"

    def test_resolution_1080p_accepted(self) -> None:
        inp = SeedanceCreateTaskInput(prompt="test", resolution="1080p")
        assert inp.resolution == "1080p"

    def test_resolution_4k_accepted_statically(self) -> None:
        assert SeedanceCreateTaskInput(prompt="test", resolution="4k").resolution == "4k"

    def test_30_images_accepted(self) -> None:
        images = [
            SeedanceImageInput(kind="url", url=f"https://example.com/img{i}.jpg") for i in range(30)
        ]
        inp = SeedanceCreateTaskInput(prompt="test", images=images)
        assert len(inp.images) == 30

    def test_31_images_rejected(self) -> None:
        images = [
            SeedanceImageInput(kind="url", url=f"https://example.com/img{i}.jpg") for i in range(31)
        ]
        with pytest.raises(ValidationError):
            SeedanceCreateTaskInput(prompt="test", images=images)

    def test_10_videos_accepted(self) -> None:
        videos = [SeedanceVideoInput(url=f"https://example.com/vid{i}.mp4") for i in range(10)]
        inp = SeedanceCreateTaskInput(prompt="test", videos=videos)
        assert len(inp.videos) == 10

    def test_11_videos_rejected(self) -> None:
        videos = [SeedanceVideoInput(url=f"https://example.com/vid{i}.mp4") for i in range(11)]
        with pytest.raises(ValidationError):
            SeedanceCreateTaskInput(prompt="test", videos=videos)

    def test_10_audios_accepted(self) -> None:
        videos = [SeedanceVideoInput(url="https://example.com/vid.mp4")]
        audios = [
            SeedanceAudioInput(kind="url", url=f"https://example.com/aud{i}.mp3") for i in range(10)
        ]
        inp = SeedanceCreateTaskInput(prompt="test", videos=videos, audios=audios)
        assert len(inp.audios) == 10

    def test_11_audios_rejected(self) -> None:
        videos = [SeedanceVideoInput(url="https://example.com/vid.mp4")]
        audios = [
            SeedanceAudioInput(kind="url", url=f"https://example.com/aud{i}.mp3") for i in range(11)
        ]
        with pytest.raises(ValidationError):
            SeedanceCreateTaskInput(prompt="test", videos=videos, audios=audios)

    def test_audio_only_accepted(self) -> None:
        """Seedance 2.5 supports audio-only input (unique to 2.5)."""
        audios = [SeedanceAudioInput(kind="url", url="https://example.com/aud.mp3")]
        inp = SeedanceCreateTaskInput(audios=audios)
        assert inp.audios is not None
        assert len(inp.audios) == 1

    def test_audio_only_with_prompt_accepted(self) -> None:
        """Audio + prompt is also valid."""
        audios = [SeedanceAudioInput(kind="url", url="https://example.com/aud.mp3")]
        inp = SeedanceCreateTaskInput(prompt="dancing to the beat", audios=audios)
        assert inp.prompt == "dancing to the beat"
        assert len(inp.audios or []) == 1

    def test_no_prompt_no_media_rejected(self) -> None:
        with pytest.raises(ValidationError, match="At least one"):
            SeedanceCreateTaskInput()

    @pytest.mark.parametrize("task_type", ["auto", "reference", "edit", "extend"])
    def test_documented_task_types_accepted(self, task_type: str) -> None:
        inp = SeedanceCreateTaskInput(
            prompt="work with the reference video",
            videos=["https://example.com/v.mp4"],
            omni_reference_task_type=task_type,
        )
        assert inp.omni_reference_task_type == task_type

    @pytest.mark.parametrize("task_type", ["edit_video", "extend_video", "EDIT", ""])
    def test_undocumented_task_types_rejected(self, task_type: str) -> None:
        with pytest.raises(ValidationError, match="omni_reference_task_type"):
            SeedanceCreateTaskInput(
                prompt="edit",
                videos=["https://example.com/v.mp4"],
                omni_reference_task_type=task_type,
            )

    def test_omni_reference_task_type_none_default(self) -> None:
        inp = SeedanceCreateTaskInput(prompt="test")
        assert inp.omni_reference_task_type is None

    @pytest.mark.parametrize("task_type", ["edit", "extend"])
    def test_edit_and_extend_require_a_reference_video(self, task_type: str) -> None:
        with pytest.raises(ValidationError, match="requires at least one reference video"):
            SeedanceCreateTaskInput(prompt="change it", omni_reference_task_type=task_type)

    @pytest.mark.parametrize("task_type", ["edit", "extend"])
    def test_edit_and_extend_reject_fixed_ratio(self, task_type: str) -> None:
        with pytest.raises(ValidationError, match="ratio omitted or 'adaptive'; got '16:9'"):
            SeedanceCreateTaskInput(
                prompt="change it",
                videos=["https://example.com/v.mp4"],
                omni_reference_task_type=task_type,
                ratio="16:9",
            )

    @pytest.mark.parametrize("task_type", ["edit", "extend"])
    def test_edit_and_extend_accept_adaptive_ratio(self, task_type: str) -> None:
        inp = SeedanceCreateTaskInput(
            prompt="change it",
            videos=["https://example.com/v.mp4"],
            omni_reference_task_type=task_type,
            ratio="adaptive",
        )
        assert inp.ratio == "adaptive"

    def test_edit_rejects_explicit_duration(self) -> None:
        with pytest.raises(ValidationError, match=r"requires duration omitted or -1 .*got 10"):
            SeedanceCreateTaskInput(
                prompt="replace the background",
                videos=["https://example.com/v.mp4"],
                omni_reference_task_type="edit",
                duration=10,
            )

    def test_edit_accepts_auto_duration(self) -> None:
        inp = SeedanceCreateTaskInput(
            prompt="replace the background",
            videos=["https://example.com/v.mp4"],
            omni_reference_task_type="edit",
            duration=-1,
        )
        assert inp.duration == -1

    def test_extend_accepts_explicit_duration(self) -> None:
        inp = SeedanceCreateTaskInput(
            prompt="extend forward",
            videos=["https://example.com/v.mp4"],
            omni_reference_task_type="extend",
            duration=10,
        )
        assert inp.duration == 10

    def test_violations_are_reported_together(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            SeedanceCreateTaskInput(
                prompt="edit",
                omni_reference_task_type="edit",
                ratio="9:16",
                duration=8,
            )
        message = str(excinfo.value)
        assert "reference video" in message
        assert "ratio omitted" in message
        assert "duration omitted" in message

    @pytest.mark.parametrize("role", ["first_frame", "last_frame"])
    def test_frame_roles_reject_fixed_ratio(self, role: str) -> None:
        with pytest.raises(ValidationError, match="First-frame"):
            SeedanceCreateTaskInput(
                prompt="animate",
                images=[{"url": "https://example.com/a.png", "role": role}],
                ratio="16:9",
            )

    def test_reference_images_keep_fixed_ratio(self) -> None:
        inp = SeedanceCreateTaskInput(
            prompt="animate",
            images=["https://example.com/a.png"],
            ratio="16:9",
        )
        assert inp.ratio == "16:9"

    def test_ratio_preserved_without_task_type(self) -> None:
        inp = SeedanceCreateTaskInput(prompt="generate a video", ratio="16:9")
        assert inp.ratio == "16:9"


class TestSeedance25VariationsInput:
    """Tests for SeedanceCreateTaskInput validators."""

    def test_prompt_required_when_no_variation_prompts(self) -> None:
        """When media is provided but neither prompt nor variation_prompts, raises."""
        images = [SeedanceImageInput(kind="url", url="https://example.com/img.jpg")]
        with pytest.raises(ValidationError, match="Either prompt or variation_prompts"):
            SeedanceCreateTaskInput(variations=2, images=images)

    def test_variation_prompts_length_must_match(self) -> None:
        with pytest.raises(ValidationError, match="exactly 3"):
            SeedanceCreateTaskInput(
                variations=3,
                variation_prompts=["a", "b"],
            )

    def test_too_many_variations_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SeedanceCreateTaskInput(variations=6, prompt="test")

    def test_valid_variations(self) -> None:
        inp = SeedanceCreateTaskInput(
            variations=3,
            variation_prompts=["a cat", "a dog", "a bird"],
        )
        assert inp.variations == 3
        assert len(inp.variation_prompts) == 3

    def test_inherits_2_5_duration_limit(self) -> None:
        inp = SeedanceCreateTaskInput(prompt="test", variations=1, duration=30)
        assert inp.duration == 30

    def test_inherits_2_5_duration_rejection(self) -> None:
        with pytest.raises(ValidationError):
            SeedanceCreateTaskInput(prompt="test", variations=1, duration=31)

    def test_inherits_task_type_constraints(self) -> None:
        with pytest.raises(ValidationError, match="ratio omitted or 'adaptive'"):
            SeedanceCreateTaskInput(
                variations=2,
                variation_prompts=["extend clip a", "extend clip b"],
                videos=["https://example.com/v.mp4"],
                omni_reference_task_type="extend",
                ratio="9:16",
            )


class TestSeedance25DraftModeInput:
    """Draft mode validators shared by the Seedance 2.5 and Premium create tools."""

    def test_draft_defaults_resolution_to_480p(self) -> None:
        inp = SeedanceCreateTaskInput(prompt="test", draft=True)
        assert inp.resolution == "480p"

    def test_draft_explicit_480p_accepted(self) -> None:
        inp = SeedanceCreateTaskInput(prompt="test", draft=True, resolution="480p")
        assert inp.resolution == "480p"

    def test_draft_other_resolution_rejected(self) -> None:
        with pytest.raises(ValidationError, match="only support resolution '480p'"):
            SeedanceCreateTaskInput(prompt="test", draft=True, resolution="720p")

    def test_draft_false_keeps_resolution_unset(self) -> None:
        inp = SeedanceCreateTaskInput(prompt="test", draft=False)
        assert inp.resolution is None

    def test_draft_task_id_leaves_resolution_for_runtime(self) -> None:
        inp = SeedanceCreateTaskInput(draft_task_id="cgt-draft")
        assert inp.draft_task_id == "cgt-draft"
        assert inp.resolution is None

    @pytest.mark.parametrize("draft", [True, False])
    def test_draft_and_draft_task_id_rejected(self, draft: bool) -> None:
        with pytest.raises(ValidationError, match="draft must be omitted"):
            SeedanceCreateTaskInput(draft=draft, draft_task_id="cgt-draft")

    def test_draft_false_without_draft_task_id_accepted(self) -> None:
        assert SeedanceCreateTaskInput(prompt="test", draft=False).draft is False

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
            SeedanceCreateTaskInput.model_validate({"draft_task_id": "cgt-draft", field: value})

    def test_draft_task_id_allows_output_settings(self) -> None:
        inp = SeedanceCreateTaskInput(
            draft_task_id="cgt-draft",
            watermark=True,
            return_last_frame=True,
            priority=3,
            execution_expires_after=7200,
            safety_identifier="user-1",
        )
        assert inp.watermark is True

    def test_premium_draft_still_480p(self) -> None:
        with pytest.raises(ValidationError, match="only support resolution '480p'"):
            SeedanceCreateTaskInput(prompt="test", draft=True, resolution="4k")

    def test_variations_draft_defaults_480p(self) -> None:
        inp = SeedanceCreateTaskInput(prompt="test", variations=2, draft=True)
        assert inp.resolution == "480p"

    def test_draft_task_id_rejects_variations(self) -> None:
        with pytest.raises(ValidationError, match="renders exactly one final video"):
            SeedanceCreateTaskInput(draft_task_id="cgt-draft", variations=2)

    def test_schema_exposes_draft_and_variations_and_dry_run(self) -> None:
        props = SeedanceCreateTaskInput.model_json_schema()["properties"]
        assert {
            "draft",
            "draft_task_id",
            "variations",
            "variation_prompts",
            "dry_run",
        } <= props.keys()

    def test_duration_below_four_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least 4 seconds"):
            SeedanceCreateTaskInput(prompt="test", duration=3)

    def test_output_format_values(self) -> None:
        assert SeedanceCreateTaskInput(prompt="t", output_format="mov").output_format == "mov"
        with pytest.raises(ValidationError):
            SeedanceCreateTaskInput.model_validate({"prompt": "t", "output_format": "avi"})
