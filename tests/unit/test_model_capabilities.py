"""Unit tests for model capability registry."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from pydantic import ValidationError

from ark_mcp.config.model_capabilities import (
    ImageCapabilities,
    ModelFamily,
    Seed3DCapabilities,
    VideoCapabilities,
    get_capability_registry,
)


class TestCapabilityRegistry:
    """Tests for the model capability registry."""

    def test_get_default_image_capabilities(self) -> None:
        registry = get_capability_registry()
        caps = registry.get_image_capabilities()
        assert isinstance(caps, ImageCapabilities)
        assert caps.family in (
            ModelFamily.SEEDREAM_PRO,
            ModelFamily.SEEDREAM_LITE,
            ModelFamily.SEEDREAM_4X,
        )

    def test_get_default_video_capabilities(self) -> None:
        registry = get_capability_registry()
        caps = registry.get_video_capabilities()
        assert isinstance(caps, VideoCapabilities)
        assert caps.family in (
            ModelFamily.SEEDANCE_2,
            ModelFamily.SEEDANCE_2_FAST,
            ModelFamily.SEEDANCE_2_MINI,
            ModelFamily.SEEDANCE_2_5,
            ModelFamily.SEEDANCE_2_5_PREMIUM,
        )

    def test_invalid_image_model_raises(self) -> None:
        registry = get_capability_registry()
        with pytest.raises(ValueError, match="not in the configured"):
            registry.get_image_capabilities("nonexistent-model")

    def test_invalid_video_model_raises(self) -> None:
        registry = get_capability_registry()
        with pytest.raises(ValueError, match="not in the configured"):
            registry.get_video_capabilities("nonexistent-model")

    def test_validate_output_format_valid(self) -> None:
        registry = get_capability_registry()
        caps = registry.get_image_capabilities()
        if "png" in caps.supported_output_formats:
            assert registry.validate_output_format(None, "png") == "png"

    def test_validate_output_format_invalid_raises(self) -> None:
        registry = get_capability_registry()
        with pytest.raises(ValueError, match="not supported"):
            registry.validate_output_format(None, "gif")

    def test_validate_resolution_valid(self) -> None:
        registry = get_capability_registry()
        caps = registry.get_video_capabilities()
        if caps.supported_resolutions:
            res = caps.supported_resolutions[0]
            assert registry.validate_resolution(None, res) == res

    def test_validate_resolution_invalid_raises(self) -> None:
        registry = get_capability_registry()
        with pytest.raises(ValueError, match="not supported"):
            registry.validate_resolution(None, "9999p")

    def test_validate_duration_auto(self) -> None:
        registry = get_capability_registry()
        assert registry.validate_duration(None, -1) == -1

    def test_validate_duration_in_range(self) -> None:
        registry = get_capability_registry()
        assert registry.validate_duration(None, 5) == 5

    def test_validate_duration_out_of_range_raises(self) -> None:
        registry = get_capability_registry()
        with pytest.raises(ValueError, match="outside the supported range"):
            registry.validate_duration(None, 100)

    @pytest.mark.parametrize("duration", [0, 1, 3])
    def test_validate_duration_below_four_seconds_raises(self, duration: int) -> None:
        registry = get_capability_registry()
        with pytest.raises(ValueError, match=r"outside the supported range \[4, 15\]"):
            registry.validate_duration(None, duration)

    def test_validate_duration_minimum_four_seconds(self) -> None:
        registry = get_capability_registry()
        assert registry.validate_duration(None, 4) == 4

    def test_list_image_models(self) -> None:
        registry = get_capability_registry()
        models = registry.list_image_models()
        assert len(models) >= 1

    def test_list_video_models(self) -> None:
        registry = get_capability_registry()
        models = registry.list_video_models()
        assert len(models) >= 1


class TestPromptOptimizationDefaults:
    """Tests for Seedream prompt optimization default resolution."""

    @staticmethod
    def _pro_caps(monkeypatch: pytest.MonkeyPatch, configured_mode: str) -> ImageCapabilities:
        """Build the PRO capability under an explicitly pinned operator mode.

        The mode is always set here rather than inherited, because
        ``refresh_settings`` reads the ``.env`` in the working directory and a
        developer who configures ``fast`` locally would otherwise fail a suite
        that passes in CI.
        """
        from ark_mcp.config.env import refresh_settings
        from ark_mcp.config.model_capabilities import refresh_capability_registry

        monkeypatch.setenv("SEEDREAM_DEFAULT_MODEL", "dola-seedream-5-0-pro-260628")
        monkeypatch.setenv("SEEDREAM_MODEL_FAMILY", "pro")
        monkeypatch.setenv("SEEDREAM_PROMPT_OPTIMIZATION_MODE", configured_mode)
        monkeypatch.delenv("SEEDREAM_MODEL_BINDINGS", raising=False)
        refresh_settings()
        registry = refresh_capability_registry()
        return registry.get_image_capabilities("dola-seedream-5-0-pro-260628")

    def test_pro_family_uses_configured_standard_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The shipped default sends standard when the caller omits the field."""
        caps = self._pro_caps(monkeypatch, "standard")

        assert caps.family is ModelFamily.SEEDREAM_PRO
        assert caps.default_prompt_optimization_mode == "standard"
        assert caps.resolve_prompt_optimization(None) == "standard"
        assert caps.resolve_prompt_optimization("standard") == "standard"
        assert caps.resolve_prompt_optimization("fast") == "fast"

    def test_pro_family_honors_configured_fast_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SEEDREAM_PROMPT_OPTIMIZATION_MODE=fast changes only the omitted-value case."""
        caps = self._pro_caps(monkeypatch, "fast")

        assert caps.default_prompt_optimization_mode == "fast"
        assert caps.resolve_prompt_optimization(None) == "fast"
        assert caps.resolve_prompt_optimization("standard") == "standard"

    def test_invalid_configured_mode_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An unsupported operator value fails closed at settings validation."""
        from ark_mcp.config.env import refresh_settings

        monkeypatch.setenv("SEEDREAM_PROMPT_OPTIMIZATION_MODE", "turbo")
        with pytest.raises(ValidationError):
            refresh_settings()

    def test_family_without_default_uses_provider_mode(self) -> None:
        """Families without a default send no optimize_prompt_options unless requested."""
        caps = ImageCapabilities(
            family=ModelFamily.SEEDREAM_LITE,
            model_id="test-lite-model",
            max_references=14,
            supports_batch=True,
            supports_streaming=True,
            supported_output_formats=("png", "jpeg"),
        )
        assert caps.default_prompt_optimization_mode is None
        assert caps.resolve_prompt_optimization(None) is None
        assert caps.resolve_prompt_optimization("standard") == "standard"

    def test_resolver_disabled_capability_returns_none(self) -> None:
        """When prompt optimization is unsupported, no mode is ever sent."""
        caps = ImageCapabilities(
            family=ModelFamily.SEEDREAM_PRO,
            model_id="test-pro-model",
            max_references=10,
            supports_batch=False,
            supports_streaming=False,
            supported_output_formats=("png", "jpeg"),
            supports_prompt_optimization=False,
            default_prompt_optimization_mode="fast",
        )
        assert caps.resolve_prompt_optimization(None) is None
        assert caps.resolve_prompt_optimization("standard") is None


class TestSeedance25Capabilities:
    """Tests for Seedance 2.5 model capabilities."""

    def test_seedance_2_5_capabilities_with_bindings(self) -> None:
        """When SEEDANCE_MODEL_BINDINGS includes a 2.5 binding, caps are correct."""
        import os

        from ark_mcp.config.env import refresh_settings
        from ark_mcp.config.model_capabilities import refresh_capability_registry

        old_bindings = os.environ.get("SEEDANCE_MODEL_BINDINGS", "")
        old_default = os.environ.get("SEEDANCE_DEFAULT_MODEL", "")
        try:
            os.environ["SEEDANCE_DEFAULT_MODEL"] = "dreamina-seedance-2-5-260628"
            os.environ["SEEDANCE_MODEL_FAMILY"] = "seedance_2_5"
            os.environ.pop("SEEDANCE_MODEL_BINDINGS", None)
            refresh_settings()
            registry = refresh_capability_registry()

            caps = registry.get_video_capabilities("dreamina-seedance-2-5-260628")
            assert caps.family is ModelFamily.SEEDANCE_2_5
            assert caps.duration_range == (4, 30)
            assert caps.max_reference_images == 30
            assert caps.max_reference_videos == 10
            assert caps.max_reference_audios == 10
            assert caps.supported_resolutions == ("480p", "720p", "1080p")
        finally:
            os.environ["SEEDANCE_MODEL_BINDINGS"] = old_bindings
            os.environ["SEEDANCE_DEFAULT_MODEL"] = old_default
            refresh_settings()
            refresh_capability_registry()

    def test_seedance_2_5_duration_validation(self) -> None:
        """2.5 model accepts duration up to 30 but not 31."""
        import os

        from ark_mcp.config.env import refresh_settings
        from ark_mcp.config.model_capabilities import refresh_capability_registry

        old_default = os.environ.get("SEEDANCE_DEFAULT_MODEL", "")
        try:
            os.environ["SEEDANCE_DEFAULT_MODEL"] = "dreamina-seedance-2-5-260628"
            os.environ["SEEDANCE_MODEL_FAMILY"] = "seedance_2_5"
            os.environ.pop("SEEDANCE_MODEL_BINDINGS", None)
            refresh_settings()
            registry = refresh_capability_registry()

            assert registry.validate_duration("dreamina-seedance-2-5-260628", 30) == 30
            assert registry.validate_duration("dreamina-seedance-2-5-260628", -1) == -1
            with pytest.raises(ValueError, match="outside the supported range"):
                registry.validate_duration("dreamina-seedance-2-5-260628", 31)
            with pytest.raises(ValueError, match="outside the supported range"):
                registry.validate_duration("dreamina-seedance-2-5-260628", 3)
        finally:
            os.environ["SEEDANCE_DEFAULT_MODEL"] = old_default
            refresh_settings()
            refresh_capability_registry()

    def test_seedance_2_5_resolution_validation(self) -> None:
        """2.5 model accepts 480p/720p/1080p but not 4k."""
        import os

        from ark_mcp.config.env import refresh_settings
        from ark_mcp.config.model_capabilities import refresh_capability_registry

        old_default = os.environ.get("SEEDANCE_DEFAULT_MODEL", "")
        try:
            os.environ["SEEDANCE_DEFAULT_MODEL"] = "dreamina-seedance-2-5-260628"
            os.environ["SEEDANCE_MODEL_FAMILY"] = "seedance_2_5"
            os.environ.pop("SEEDANCE_MODEL_BINDINGS", None)
            refresh_settings()
            registry = refresh_capability_registry()

            assert registry.validate_resolution("dreamina-seedance-2-5-260628", "480p") == "480p"
            assert registry.validate_resolution("dreamina-seedance-2-5-260628", "720p") == "720p"
            assert registry.validate_resolution("dreamina-seedance-2-5-260628", "1080p") == "1080p"
            with pytest.raises(ValueError, match="not supported"):
                registry.validate_resolution("dreamina-seedance-2-5-260628", "4k")
        finally:
            os.environ["SEEDANCE_DEFAULT_MODEL"] = old_default
            refresh_settings()
            refresh_capability_registry()


class TestSeed3DCapabilities:
    """Tests for the Seed3D capability registry."""

    def test_hyper3d_default_resolution(self) -> None:
        from ark_mcp.config.env import get_settings

        registry = get_capability_registry()
        settings = get_settings()
        caps = registry.get_seed3d_capabilities(default_model=settings.hyper3d_default_model)
        assert isinstance(caps, Seed3DCapabilities)
        assert caps.family is ModelFamily.SEED3D_HYPER3D
        assert caps.supports_text_to_3d is True
        assert caps.max_reference_images == 5

    def test_hitem3d_default_resolution(self) -> None:
        from ark_mcp.config.env import get_settings

        registry = get_capability_registry()
        settings = get_settings()
        caps = registry.get_seed3d_capabilities(default_model=settings.hitem3d_default_model)
        assert isinstance(caps, Seed3DCapabilities)
        assert caps.family is ModelFamily.SEED3D_HITEM3D
        assert caps.supports_text_to_3d is False
        assert caps.max_reference_images == 4

    def test_unknown_seed3d_model_raises(self) -> None:
        registry = get_capability_registry()
        with pytest.raises(ValueError, match="not in the configured Seed3D"):
            registry.get_seed3d_capabilities("not-a-real-model")


class TestSeedance25PremiumCapabilities:
    """Tests for whitelist-only Seedance 2.5 Premium capabilities."""

    @pytest.fixture(autouse=True)
    def _premium_enabled(self, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
        from ark_mcp.config.env import refresh_settings
        from ark_mcp.config.model_capabilities import refresh_capability_registry

        monkeypatch.setenv(
            "SEEDANCE_MODEL_BINDINGS",
            '[{"model_id":"dreamina-seedance-2-0-260128","family":"standard"},'
            '{"model_id":"dreamina-seedance-2-5-260628","family":"seedance_2_5"}]',
        )
        monkeypatch.setenv("BYTEPLUS_MODELARK_SEEDANCE_2_5_PREMIUM_ENABLED", "true")
        refresh_settings()
        refresh_capability_registry()
        yield
        monkeypatch.undo()
        refresh_settings()
        refresh_capability_registry()

    def test_premium_capabilities(self) -> None:
        registry = get_capability_registry()
        caps = registry.get_video_capabilities("dreamina-seedance-2-5-premium-260915")
        assert caps.family is ModelFamily.SEEDANCE_2_5_PREMIUM
        assert caps.supported_resolutions == ("480p", "720p", "1080p", "4k")
        assert caps.duration_range == (4, 30)
        assert caps.max_reference_images == 30
        assert caps.max_reference_videos == 10
        assert caps.max_reference_audios == 10

    def test_premium_accepts_4k_regular_2_5_does_not(self) -> None:
        registry = get_capability_registry()
        assert registry.validate_resolution("dreamina-seedance-2-5-premium-260915", "4k") == "4k"
        with pytest.raises(ValueError, match="not supported"):
            registry.validate_resolution("dreamina-seedance-2-5-260628", "4k")


@pytest.fixture
def two_seedance_bindings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from ark_mcp.config.env import get_settings
    from ark_mcp.config.model_capabilities import refresh_capability_registry

    monkeypatch.setenv(
        "SEEDANCE_MODEL_BINDINGS",
        '[{"model_id":"dreamina-seedance-2-0-260128","family":"standard"},'
        '{"model_id":"dreamina-seedance-2-5-260628","family":"seedance_2_5"}]',
    )
    get_settings.cache_clear()
    refresh_capability_registry()
    yield
    get_settings.cache_clear()
    refresh_capability_registry()


class TestExactModelResolution:
    """``resolve_*_model`` accepts exact configured IDs and suggests near misses."""

    def test_exact_id_resolves(self, two_seedance_bindings: None) -> None:
        caps = get_capability_registry().resolve_video_model("dreamina-seedance-2-5-260628")
        assert caps.family is ModelFamily.SEEDANCE_2_5
        assert caps.supports_draft is True
        assert caps.draft_final_resolution == "1080p"
        assert caps.supported_output_formats == ("mp4", "mov")

    def test_omitted_model_uses_configured_default(self, two_seedance_bindings: None) -> None:
        caps = get_capability_registry().resolve_video_model(None)
        assert caps.model_id == "dreamina-seedance-2-0-260128"

    def test_family_name_is_rejected_with_suggestion(self, two_seedance_bindings: None) -> None:
        with pytest.raises(ValueError) as excinfo:
            get_capability_registry().resolve_video_model("dreamina-seedance-2.5")
        message = str(excinfo.value)
        assert "is not configured" in message
        assert "Did you mean 'dreamina-seedance-2-5-260628'?" in message
        assert "ark_list_models(kind='video')" in message

    def test_unrelated_id_gets_no_suggestion(self, two_seedance_bindings: None) -> None:
        with pytest.raises(ValueError) as excinfo:
            get_capability_registry().resolve_video_model("xyz")
        assert "Did you mean" not in str(excinfo.value)

    def test_seedance_2_has_no_2_5_only_capabilities(self, two_seedance_bindings: None) -> None:
        caps = get_capability_registry().resolve_video_model("dreamina-seedance-2-0-260128")
        assert caps.supports_draft is False
        assert caps.supported_task_types == ()
        assert caps.supported_output_formats == ()
        assert caps.supports_audio_only_input is False

    def test_image_default_supports_transparent_background(self) -> None:
        caps = get_capability_registry().resolve_image_model(None)
        assert caps.family is ModelFamily.SEEDREAM_PRO
        assert caps.supports_transparent_background is True

    def test_understanding_routes_audio_to_audio_model(self) -> None:
        from ark_mcp.config.env import get_settings

        settings = get_settings()
        registry = get_capability_registry()
        with_audio = registry.resolve_understanding_model(None, has_audio=True)
        without_audio = registry.resolve_understanding_model(None, has_audio=False)
        assert with_audio.model_id == settings.seed_audio_understanding_model
        assert with_audio.supports_audio is True
        assert with_audio.supports_image is True
        assert without_audio.model_id == settings.seed_understanding_default_model
        assert without_audio.supports_audio is False

    def test_explicit_understanding_model_wins_over_routing(self) -> None:
        from ark_mcp.config.env import get_settings

        default = get_settings().seed_understanding_default_model
        caps = get_capability_registry().resolve_understanding_model(default, has_audio=True)
        assert caps.model_id == default

    def test_task_model_lookup_covers_video_and_unknown(self, two_seedance_bindings: None) -> None:
        registry = get_capability_registry()
        assert registry.resolve_task_model("dreamina-seedance-2-5-260628") is not None
        assert registry.resolve_task_model("not-a-model") is None


def test_audio_model_already_bound_as_seed_2_1_gains_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    from ark_mcp.config.env import get_settings
    from ark_mcp.config.model_capabilities import refresh_capability_registry

    monkeypatch.setenv("SEED_AUDIO_UNDERSTANDING_MODEL", "dola-seed-2-1-turbo-260628")
    get_settings.cache_clear()
    try:
        registry = refresh_capability_registry()
        caps = registry.get_understanding_capabilities("dola-seed-2-1-turbo-260628")
        assert caps.family is ModelFamily.SEED_2_1_TURBO
        assert caps.supports_audio is True
        assert registry.list_understanding_models().count("dola-seed-2-1-turbo-260628") == 1
    finally:
        get_settings.cache_clear()
        refresh_capability_registry()
