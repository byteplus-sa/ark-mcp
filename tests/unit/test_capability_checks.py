"""Tests for capability-driven Seedance request validation."""

from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from ark_mcp.config.model_capabilities import (
    SEEDANCE_2_5_TASK_TYPES,
    ModelFamily,
    VideoCapabilities,
)
from ark_mcp.tools._capability_checks import check_video_request, raise_on_violations

SEEDANCE_2 = VideoCapabilities(
    family=ModelFamily.SEEDANCE_2,
    model_id="dreamina-seedance-2-0-260128",
    max_reference_images=9,
    max_reference_videos=3,
    max_reference_audios=3,
    supported_resolutions=("480p", "720p", "1080p", "4k"),
    supports_seed=False,
    supports_camera_fixed=False,
    supports_frames=False,
    supports_service_tier_flex=False,
    duration_range=(4, 15),
    priority_range=(0, 9),
    execution_expires_after_range=(3600, 259200),
)
SEEDANCE_2_5 = replace(
    SEEDANCE_2,
    family=ModelFamily.SEEDANCE_2_5,
    model_id="dreamina-seedance-2-5-260628",
    max_reference_images=30,
    max_reference_videos=10,
    max_reference_audios=10,
    supported_resolutions=("480p", "720p", "1080p"),
    duration_range=(4, 30),
    supports_draft=True,
    draft_final_resolution="1080p",
    supports_audio_only_input=True,
    supported_task_types=SEEDANCE_2_5_TASK_TYPES,
    supported_output_formats=("mp4", "mov"),
)


@dataclass
class Request:
    resolution: str | None = None
    ratio: str | None = None
    duration: int | None = None
    priority: int | None = None
    execution_expires_after: int | None = None
    images: list[object] | None = None
    videos: list[object] | None = None
    audios: list[object] | None = None
    draft: bool | None = None
    draft_task_id: str | None = None
    omni_reference_task_type: str | None = None
    output_format: str | None = None


def test_empty_request_is_valid_for_every_family() -> None:
    assert check_video_request(Request(), SEEDANCE_2) == []
    assert check_video_request(Request(), SEEDANCE_2_5) == []


@pytest.mark.parametrize(
    ("caps", "resolution", "valid"),
    [(SEEDANCE_2, "4k", True), (SEEDANCE_2_5, "4k", False), (SEEDANCE_2_5, "1080p", True)],
)
def test_resolution_is_checked_per_model(
    caps: VideoCapabilities, resolution: str, valid: bool
) -> None:
    violations = check_video_request(Request(resolution=resolution), caps)
    assert (violations == []) is valid


@pytest.mark.parametrize(
    ("caps", "duration", "valid"),
    [
        (SEEDANCE_2, -1, True),
        (SEEDANCE_2, 0, False),
        (SEEDANCE_2, 3, False),
        (SEEDANCE_2, 4, True),
        (SEEDANCE_2, 15, True),
        (SEEDANCE_2, 16, False),
        (SEEDANCE_2_5, 30, True),
        (SEEDANCE_2_5, 31, False),
    ],
)
def test_duration_bounds_are_inclusive_with_auto(
    caps: VideoCapabilities, duration: int, valid: bool
) -> None:
    violations = check_video_request(Request(duration=duration), caps)
    assert (violations == []) is valid


@pytest.mark.parametrize(
    ("field", "value", "valid"),
    [
        ("priority", 0, True),
        ("priority", 9, True),
        ("priority", 10, False),
        ("execution_expires_after", 3600, True),
        ("execution_expires_after", 3599, False),
        ("execution_expires_after", 259201, False),
    ],
)
def test_priority_and_expiry_ranges(field: str, value: int, valid: bool) -> None:
    violations = check_video_request(Request(**{field: value}), SEEDANCE_2)
    assert (violations == []) is valid


@pytest.mark.parametrize(("field", "limit"), [("images", 9), ("videos", 3), ("audios", 3)])
def test_reference_counts_at_and_over_the_limit(field: str, limit: int) -> None:
    media = {"images": [object()]} if field == "audios" else {}
    at_limit = Request(**{field: [object()] * limit, **media})
    over_limit = Request(**{field: [object()] * (limit + 1), **media})
    assert check_video_request(at_limit, SEEDANCE_2) == []
    assert check_video_request(over_limit, SEEDANCE_2) == [
        f"{limit + 1} reference {field} exceed the maximum of {limit}"
    ]


def test_draft_is_rejected_on_models_without_draft() -> None:
    assert check_video_request(Request(draft=True), SEEDANCE_2) != []
    assert check_video_request(Request(draft_task_id="cgt-1"), SEEDANCE_2) != []
    assert check_video_request(Request(draft=True), SEEDANCE_2_5) == []


def test_audio_only_input_depends_on_model() -> None:
    audio_only = Request(audios=[object()])
    assert check_video_request(audio_only, SEEDANCE_2) == [
        "audio cannot be the only media input; add an image or video"
    ]
    assert check_video_request(audio_only, SEEDANCE_2_5) == []


def test_task_type_and_output_format_are_rejected_on_2_0() -> None:
    violations = check_video_request(
        Request(omni_reference_task_type="edit", output_format="mov"), SEEDANCE_2
    )
    assert len(violations) == 2
    assert "allowed: none" in violations[0]


def test_unsupported_ratio_is_rejected() -> None:
    assert check_video_request(Request(ratio="2:1"), SEEDANCE_2_5) != []
    assert check_video_request(Request(ratio="adaptive"), SEEDANCE_2_5) == []


def test_every_violation_is_reported_at_once() -> None:
    violations = check_video_request(
        Request(resolution="4k", duration=31, priority=10, images=[object()] * 31),
        SEEDANCE_2_5,
    )
    assert len(violations) == 4


def test_raise_on_violations_lists_each_problem_and_points_to_discovery() -> None:
    with pytest.raises(ValueError) as excinfo:
        raise_on_violations(
            ["first problem", "second problem"],
            model_id="dreamina-seedance-2-5-260628",
            family="seedance_2_5",
            kind="video",
        )
    message = str(excinfo.value)
    assert "- first problem\n- second problem" in message
    assert "ark_list_models(kind='video')" in message


def test_raise_on_violations_is_silent_without_violations() -> None:
    raise_on_violations([], model_id="m", family="f", kind="video")
