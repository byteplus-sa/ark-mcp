"""Unit tests for cost estimation logic."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from ark_mcp.tools._cost import (
    COST_PER_AUDIO_SECOND,
    COST_PER_IMAGE,
    COST_PER_VIDEO_TASK,
    COST_PER_VIDEO_TASK_2_5,
    DEFAULT_MAX_CONCURRENT,
    estimate_cost,
    log_cost_estimate,
    register_video_cost_override,
)


class TestEstimateCostImage:
    """Tests for estimate_cost with image product."""

    def test_single_image(self) -> None:
        cost = estimate_cost(product="image", variations=1)
        assert cost == 0.03

    def test_multiple_images(self) -> None:
        cost = estimate_cost(product="image", variations=5)
        assert cost == round(5 * COST_PER_IMAGE, 2)

    def test_zero_variations_image(self) -> None:
        cost = estimate_cost(product="image", variations=0)
        assert cost == 0.0

    def test_large_count_images(self) -> None:
        cost = estimate_cost(product="image", variations=1000)
        assert cost == round(1000 * COST_PER_IMAGE, 2)

    def test_image_ignores_duration(self) -> None:
        cost = estimate_cost(product="image", variations=1, duration_seconds=30.0)
        assert cost == 0.03


class TestEstimateCostAudio:
    """Tests for estimate_cost with audio product."""

    def test_audio_with_duration_above_minimum(self) -> None:
        cost = estimate_cost(product="audio", variations=1, duration_seconds=30.0)
        assert cost == round(30 * COST_PER_AUDIO_SECOND, 2)

    def test_audio_clamps_to_minimum_duration(self) -> None:
        cost = estimate_cost(product="audio", variations=1, duration_seconds=5.0)
        assert cost == round(10 * COST_PER_AUDIO_SECOND, 2)

    def test_audio_clamps_zero_duration(self) -> None:
        cost = estimate_cost(product="audio", variations=1, duration_seconds=0.0)
        assert cost == round(10 * COST_PER_AUDIO_SECOND, 2)

    def test_audio_multiple_variations(self) -> None:
        cost = estimate_cost(product="audio", variations=3, duration_seconds=20.0)
        assert cost == round(3 * 20 * COST_PER_AUDIO_SECOND, 2)

    def test_audio_zero_variations(self) -> None:
        cost = estimate_cost(product="audio", variations=0, duration_seconds=30.0)
        assert cost == 0.0

    def test_audio_exactly_minimum_duration(self) -> None:
        cost = estimate_cost(product="audio", variations=1, duration_seconds=10.0)
        assert cost == round(10 * COST_PER_AUDIO_SECOND, 2)

    def test_audio_fractional_duration(self) -> None:
        cost = estimate_cost(product="audio", variations=1, duration_seconds=2.5)
        assert cost == round(10 * COST_PER_AUDIO_SECOND, 2)

    def test_audio_just_below_minimum(self) -> None:
        cost = estimate_cost(product="audio", variations=1, duration_seconds=9.9)
        assert cost == round(10 * COST_PER_AUDIO_SECOND, 2)

    def test_audio_large_duration(self) -> None:
        cost = estimate_cost(product="audio", variations=1, duration_seconds=3600.0)
        assert cost == round(3600 * COST_PER_AUDIO_SECOND, 2)


class TestEstimateCostVideo:
    """Tests for estimate_cost with video product."""

    def test_single_video_task(self) -> None:
        cost = estimate_cost(product="video", variations=1)
        assert cost == 0.07

    def test_multiple_video_tasks(self) -> None:
        cost = estimate_cost(product="video", variations=4)
        assert cost == round(4 * COST_PER_VIDEO_TASK, 2)

    def test_video_zero_variations(self) -> None:
        cost = estimate_cost(product="video", variations=0)
        assert cost == 0.0

    def test_video_ignores_duration(self) -> None:
        cost = estimate_cost(product="video", variations=1, duration_seconds=60.0)
        assert cost == 0.07

    def test_video_large_count(self) -> None:
        cost = estimate_cost(product="video", variations=500)
        assert cost == round(500 * COST_PER_VIDEO_TASK, 2)


class TestEstimateCostUnknown:
    """Tests for estimate_cost with unknown product."""

    def test_unknown_product_returns_zero(self) -> None:
        cost = estimate_cost(product="unknown", variations=10)
        assert cost == 0.0

    def test_unknown_product_with_duration_returns_zero(self) -> None:
        cost = estimate_cost(product="text", variations=5, duration_seconds=30.0)
        assert cost == 0.0

    def test_empty_product_returns_zero(self) -> None:
        cost = estimate_cost(product="", variations=1)
        assert cost == 0.0


class TestCostConstants:
    """Verify cost per unit values are reasonable."""

    def test_image_cost_is_positive(self) -> None:
        assert COST_PER_IMAGE > 0

    def test_audio_cost_per_second_is_positive(self) -> None:
        assert COST_PER_AUDIO_SECOND > 0

    def test_video_cost_is_positive(self) -> None:
        assert COST_PER_VIDEO_TASK > 0

    def test_video_more_expensive_than_image(self) -> None:
        assert COST_PER_VIDEO_TASK > COST_PER_IMAGE

    def test_default_max_concurrent_is_positive(self) -> None:
        assert DEFAULT_MAX_CONCURRENT > 0


class TestLogCostEstimate:
    """Tests for log_cost_estimate function."""

    def test_returns_same_cost_as_estimate_image(self, capsys: pytest.CaptureFixture[str]) -> None:
        cost = log_cost_estimate(product="image", variations=3)
        expected = estimate_cost(product="image", variations=3)
        assert cost == expected

    def test_returns_same_cost_as_estimate_audio(self, capsys: pytest.CaptureFixture[str]) -> None:
        cost = log_cost_estimate(product="audio", variations=2, duration_seconds=15.0)
        expected = estimate_cost(product="audio", variations=2, duration_seconds=15.0)
        assert cost == expected

    def test_returns_same_cost_as_estimate_video(self, capsys: pytest.CaptureFixture[str]) -> None:
        cost = log_cost_estimate(product="video", variations=5)
        expected = estimate_cost(product="video", variations=5)
        assert cost == expected

    def test_logs_cost_estimate_with_fields(self, capsys: pytest.CaptureFixture[str]) -> None:
        capsys.readouterr()
        log_cost_estimate(product="image", variations=2)
        captured = capsys.readouterr()
        record = json.loads(captured.err.strip())
        assert record["msg"] == "cost_estimate"
        assert record["product"] == "image"
        assert record["variations"] == 2
        assert record["estimated_cost_usd"] == round(2 * COST_PER_IMAGE, 2)

    def test_logs_audio_cost_with_duration(self, capsys: pytest.CaptureFixture[str]) -> None:
        capsys.readouterr()
        log_cost_estimate(product="audio", variations=3, duration_seconds=20.0)
        captured = capsys.readouterr()
        record = json.loads(captured.err.strip())
        assert record["msg"] == "cost_estimate"
        assert record["product"] == "audio"
        assert record["variations"] == 3
        assert record["estimated_cost_usd"] == round(3 * 20 * COST_PER_AUDIO_SECOND, 2)

    def test_logs_video_cost(self, capsys: pytest.CaptureFixture[str]) -> None:
        capsys.readouterr()
        log_cost_estimate(product="video", variations=2)
        captured = capsys.readouterr()
        record = json.loads(captured.err.strip())
        assert record["msg"] == "cost_estimate"
        assert record["product"] == "video"
        assert record["variations"] == 2
        assert record["estimated_cost_usd"] == round(2 * COST_PER_VIDEO_TASK, 2)

    def test_log_cost_estimate_calls_logger(self, capsys: pytest.CaptureFixture[str]) -> None:
        capsys.readouterr()
        with patch("ark_mcp.tools._cost.log_info") as mock_log:
            log_cost_estimate(product="image", variations=1)
            mock_log.assert_called_once_with(
                "cost_estimate",
                product="image",
                variations=1,
                estimated_cost_usd=0.03,
            )


class TestEstimateCost3D:
    """Tests for estimate_cost with 3d product."""

    def test_hyper3d_default(self) -> None:
        from ark_mcp.tools._cost import COST_PER_3D_TASK_HYPER3D

        assert estimate_cost(product="3d", variations=1) == COST_PER_3D_TASK_HYPER3D

    def test_hitem3d_family(self) -> None:
        from ark_mcp.tools._cost import COST_PER_3D_TASK_HITEM3D

        cost = estimate_cost(product="3d", variations=2, seed3d_family="seed3d_hitem3d")
        assert cost == round(2 * COST_PER_3D_TASK_HITEM3D, 2)

    def test_hyper3d_family(self) -> None:
        from ark_mcp.tools._cost import COST_PER_3D_TASK_HYPER3D

        cost = estimate_cost(product="3d", variations=1, seed3d_family="seed3d_hyper3d")
        assert cost == COST_PER_3D_TASK_HYPER3D


class TestVideoCostByFamily:
    """Video pricing follows the registry family, not the model ID text."""

    def test_custom_endpoint_bound_to_2_5_is_priced_as_2_5(self) -> None:
        cost = estimate_cost(
            product="video", variations=1, model_id="ep-my-endpoint", video_family="seedance_2_5"
        )
        assert cost == COST_PER_VIDEO_TASK_2_5

    def test_premium_family_is_priced_as_2_5(self) -> None:
        cost = estimate_cost(product="video", variations=2, video_family="seedance_2_5_premium")
        assert cost == round(2 * COST_PER_VIDEO_TASK_2_5, 2)

    def test_2_0_family_uses_default_price_even_with_2_5_looking_id(self) -> None:
        cost = estimate_cost(
            product="video",
            variations=1,
            model_id="dreamina-seedance-2-5-lookalike",
            video_family="seedance_2",
        )
        assert cost == COST_PER_VIDEO_TASK

    def test_model_override_wins_over_family(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("ark_mcp.tools._cost._VIDEO_COST_OVERRIDES", {})
        register_video_cost_override("ep-special", 1.23)
        cost = estimate_cost(
            product="video", variations=1, model_id="ep-special", video_family="seedance_2_5"
        )
        assert cost == 1.23
