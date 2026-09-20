"""Unit tests for validate() and get_settings() in environment configuration."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ark_mcp.config.env import Settings, get_settings, resolve_env_file, validate


@pytest.fixture
def clean_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BYTEPLUS_MODELARK_API_KEY", "sk-test-modelark")
    monkeypatch.setenv("BYTEPLUS_SEED_SPEECH_API_KEY", "sk-test-speech")
    monkeypatch.setenv("BYTEPLUS_VOD_MEDIAKIT_API_KEY", "test-mediakit-key")
    monkeypatch.setenv("BYTEPLUS_MODELARK_BASE_URL", "https://ark.test.example.com/api/v3")
    monkeypatch.setenv("BYTEPLUS_SEED_AUDIO_BASE_URL", "https://voice.test.example.com")
    monkeypatch.setenv("BYTEPLUS_VOD_MEDIAKIT_BASE_URL", "https://mediakit.test.example.com/api/v1")
    monkeypatch.setenv("SEED_SPEECH_ASR_BASE_URL", "https://voice.test.example.com")
    monkeypatch.setenv("ARTIFACT_TTL_SECONDS", "3600")
    monkeypatch.setenv("MCP_INLINE_MEDIA_MAX_BYTES", "8388608")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestValidate:
    """Tests for the validate() function."""

    def test_validate_passes_with_https_urls(self, clean_settings: None) -> None:
        validate()

    def test_validate_passes_with_default_urls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        get_settings.cache_clear()
        monkeypatch.delenv("BYTEPLUS_MODELARK_BASE_URL", raising=False)
        monkeypatch.delenv("BYTEPLUS_SEED_AUDIO_BASE_URL", raising=False)
        monkeypatch.delenv("BYTEPLUS_VOD_MEDIAKIT_BASE_URL", raising=False)
        monkeypatch.delenv("SEED_SPEECH_ASR_BASE_URL", raising=False)
        validate()
        get_settings.cache_clear()

    def test_validate_rejects_non_https_modelark_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BYTEPLUS_MODELARK_BASE_URL", "http://ark.example.com/api/v3")
        monkeypatch.setenv("BYTEPLUS_SEED_AUDIO_BASE_URL", "https://voice.example.com")
        monkeypatch.setenv("SEED_SPEECH_ASR_BASE_URL", "https://voice.example.com")
        monkeypatch.setenv("ARTIFACT_TTL_SECONDS", "3600")
        monkeypatch.setenv("MCP_INLINE_MEDIA_MAX_BYTES", "8388608")
        get_settings.cache_clear()
        with pytest.raises(ValueError, match="BYTEPLUS_MODELARK_BASE_URL must use HTTPS"):
            validate()
        get_settings.cache_clear()

    def test_validate_rejects_non_https_seed_audio_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BYTEPLUS_MODELARK_BASE_URL", "https://ark.example.com/api/v3")
        monkeypatch.setenv("BYTEPLUS_SEED_AUDIO_BASE_URL", "http://voice.example.com")
        monkeypatch.setenv("SEED_SPEECH_ASR_BASE_URL", "https://voice.example.com")
        monkeypatch.setenv("ARTIFACT_TTL_SECONDS", "3600")
        monkeypatch.setenv("MCP_INLINE_MEDIA_MAX_BYTES", "8388608")
        get_settings.cache_clear()
        with pytest.raises(ValueError, match="BYTEPLUS_SEED_AUDIO_BASE_URL must use HTTPS"):
            validate()
        get_settings.cache_clear()

    def test_validate_rejects_non_https_vod_mediakit_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BYTEPLUS_VOD_MEDIAKIT_BASE_URL", "http://mediakit.example.com/api/v1")
        get_settings.cache_clear()
        with pytest.raises(ValidationError, match="BYTEPLUS_VOD_MEDIAKIT_BASE_URL must use HTTPS"):
            Settings(_env_file=None)
        get_settings.cache_clear()

    def test_validate_rejects_non_https_asr_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BYTEPLUS_MODELARK_BASE_URL", "https://ark.example.com/api/v3")
        monkeypatch.setenv("BYTEPLUS_SEED_AUDIO_BASE_URL", "https://voice.example.com")
        monkeypatch.setenv("SEED_SPEECH_ASR_BASE_URL", "http://voice.example.com")
        monkeypatch.setenv("ARTIFACT_TTL_SECONDS", "3600")
        monkeypatch.setenv("MCP_INLINE_MEDIA_MAX_BYTES", "8388608")
        get_settings.cache_clear()
        with pytest.raises(ValidationError, match="SEED_SPEECH_ASR_BASE_URL must use HTTPS"):
            Settings(_env_file=None)
        get_settings.cache_clear()

    def test_validate_rejects_zero_ttl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BYTEPLUS_MODELARK_BASE_URL", "https://ark.example.com/api/v3")
        monkeypatch.setenv("BYTEPLUS_SEED_AUDIO_BASE_URL", "https://voice.example.com")
        monkeypatch.setenv("ARTIFACT_TTL_SECONDS", "0")
        monkeypatch.setenv("MCP_INLINE_MEDIA_MAX_BYTES", "8388608")
        get_settings.cache_clear()
        with pytest.raises(ValueError, match="ARTIFACT_TTL_SECONDS must be positive"):
            validate()
        get_settings.cache_clear()

    def test_validate_rejects_negative_ttl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BYTEPLUS_MODELARK_BASE_URL", "https://ark.example.com/api/v3")
        monkeypatch.setenv("BYTEPLUS_SEED_AUDIO_BASE_URL", "https://voice.example.com")
        monkeypatch.setenv("ARTIFACT_TTL_SECONDS", "-1")
        monkeypatch.setenv("MCP_INLINE_MEDIA_MAX_BYTES", "8388608")
        get_settings.cache_clear()
        with pytest.raises(ValueError, match="ARTIFACT_TTL_SECONDS must be positive"):
            validate()
        get_settings.cache_clear()

    def test_validate_rejects_zero_media_max_bytes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BYTEPLUS_MODELARK_BASE_URL", "https://ark.example.com/api/v3")
        monkeypatch.setenv("BYTEPLUS_SEED_AUDIO_BASE_URL", "https://voice.example.com")
        monkeypatch.setenv("ARTIFACT_TTL_SECONDS", "3600")
        monkeypatch.setenv("MCP_INLINE_MEDIA_MAX_BYTES", "0")
        get_settings.cache_clear()
        with pytest.raises(ValueError, match="MCP_INLINE_MEDIA_MAX_BYTES must be positive"):
            validate()
        get_settings.cache_clear()

    def test_validate_rejects_negative_media_max_bytes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BYTEPLUS_MODELARK_BASE_URL", "https://ark.example.com/api/v3")
        monkeypatch.setenv("BYTEPLUS_SEED_AUDIO_BASE_URL", "https://voice.example.com")
        monkeypatch.setenv("ARTIFACT_TTL_SECONDS", "3600")
        monkeypatch.setenv("MCP_INLINE_MEDIA_MAX_BYTES", "-100")
        get_settings.cache_clear()
        with pytest.raises(ValueError, match="MCP_INLINE_MEDIA_MAX_BYTES must be positive"):
            validate()
        get_settings.cache_clear()


class TestGetSettingsCaching:
    """Tests for get_settings() lru_cache behavior."""

    def test_returns_same_instance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BYTEPLUS_MODELARK_API_KEY", "sk-test")
        get_settings.cache_clear()
        first = get_settings()
        second = get_settings()
        assert first is second
        get_settings.cache_clear()

    def test_returns_new_instance_after_cache_clear(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BYTEPLUS_MODELARK_API_KEY", "sk-test-1")
        get_settings.cache_clear()
        first = get_settings()
        get_settings.cache_clear()
        monkeypatch.setenv("BYTEPLUS_MODELARK_API_KEY", "sk-test-2")
        second = get_settings()
        assert first is not second
        assert first.modelark_api_key == "sk-test-1"  # pragma: allowlist secret
        assert second.modelark_api_key == "sk-test-2"  # pragma: allowlist secret
        get_settings.cache_clear()

    def test_cache_returns_same_instance_even_after_env_change(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BYTEPLUS_MODELARK_API_KEY", "sk-test-1")
        get_settings.cache_clear()
        first = get_settings()
        monkeypatch.setenv("BYTEPLUS_MODELARK_API_KEY", "sk-test-2")
        second = get_settings()
        assert first is second
        get_settings.cache_clear()


class TestSettingsFromEnv:
    """Tests for Settings loading from environment variables."""

    def test_api_key_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BYTEPLUS_MODELARK_API_KEY", "sk-from-env")
        settings = Settings(_env_file=None)
        assert settings.modelark_api_key == "sk-from-env"  # pragma: allowlist secret

    def test_seed_speech_api_key_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BYTEPLUS_SEED_SPEECH_API_KEY", "sk-speech-env")
        settings = Settings(_env_file=None)
        assert settings.seed_speech_api_key == "sk-speech-env"  # pragma: allowlist secret

    def test_vod_mediakit_values_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BYTEPLUS_VOD_MEDIAKIT_API_KEY", "test-mediakit-env")
        monkeypatch.setenv("BYTEPLUS_VOD_MEDIAKIT_BASE_URL", "https://mediakit.example.com/api/v1")
        settings = Settings(_env_file=None)
        assert settings.vod_mediakit_api_key == "test-mediakit-env"  # pragma: allowlist secret
        assert settings.vod_mediakit_base_url == "https://mediakit.example.com/api/v1"

    def test_transport_http_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_TRANSPORT", "http")
        settings = Settings(_env_file=None)
        assert settings.mcp_transport == "http"

    def test_transport_stdio_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_TRANSPORT", "stdio")
        settings = Settings(_env_file=None)
        assert settings.mcp_transport == "stdio"

    def test_port_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_PORT", "8080")
        settings = Settings(_env_file=None)
        assert settings.mcp_port == 8080

    def test_host_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_HOST", "0.0.0.0")
        settings = Settings(_env_file=None)
        assert settings.mcp_host == "0.0.0.0"

    def test_fastmcp_alias_for_transport(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FASTMCP_TRANSPORT", "http")
        settings = Settings(_env_file=None)
        assert settings.mcp_transport == "http"

    def test_fastmcp_alias_for_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FASTMCP_HOST", "192.168.1.1")
        settings = Settings(_env_file=None)
        assert settings.mcp_host == "192.168.1.1"

    def test_fastmcp_alias_for_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FASTMCP_PORT", "9000")
        settings = Settings(_env_file=None)
        assert settings.mcp_port == 9000

    def test_allowed_origins_with_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_ALLOWED_ORIGINS", " https://a.com , https://b.com ")
        settings = Settings(_env_file=None)
        assert settings.allowed_origins == ["https://a.com", "https://b.com"]

    def test_allowed_origins_single_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_ALLOWED_ORIGINS", "https://only.com")
        settings = Settings(_env_file=None)
        assert settings.allowed_origins == ["https://only.com"]

    def test_artifact_dir_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARTIFACT_DIR", "/tmp/custom-artifacts")
        settings = Settings(_env_file=None)
        assert settings.artifact_dir == "/tmp/custom-artifacts"

    def test_connect_timeout_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BYTEPLUS_CONNECT_TIMEOUT_MS", "5000")
        settings = Settings(_env_file=None)
        assert settings.connect_timeout_ms == 5000

    def test_request_timeout_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BYTEPLUS_REQUEST_TIMEOUT_MS", "60000")
        settings = Settings(_env_file=None)
        assert settings.request_timeout_ms == 60000

    def test_base_url_overrides_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BYTEPLUS_MODELARK_BASE_URL", "https://custom.ark.com/api/v3")
        settings = Settings(_env_file=None)
        assert settings.modelark_base_url == "https://custom.ark.com/api/v3"


class TestSttConfig:
    """Tests for Seed Speech ASR (STT) configuration."""

    def test_has_stt_true_when_key_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BYTEPLUS_SEED_SPEECH_API_KEY", "sk-test-speech")
        settings = Settings(_env_file=None)
        assert settings.has_stt is True

    def test_has_stt_false_when_key_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BYTEPLUS_SEED_SPEECH_API_KEY", "")
        settings = Settings(_env_file=None)
        assert settings.has_stt is False

    def test_asr_base_url_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SEED_SPEECH_ASR_BASE_URL", raising=False)
        settings = Settings(_env_file=None)
        assert settings.seed_speech_asr_base_url == "https://voice.ap-southeast-1.bytepluses.com"

    def test_asr_base_url_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SEED_SPEECH_ASR_BASE_URL", "https://custom.asr.example.com")
        settings = Settings(_env_file=None)
        assert settings.seed_speech_asr_base_url == "https://custom.asr.example.com"

    def test_asr_poll_interval_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SEED_SPEECH_ASR_POLL_INTERVAL_SECONDS", raising=False)
        settings = Settings(_env_file=None)
        assert settings.seed_speech_asr_poll_interval_seconds == 3.0

    def test_asr_poll_interval_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SEED_SPEECH_ASR_POLL_INTERVAL_SECONDS", "5.0")
        settings = Settings(_env_file=None)
        assert settings.seed_speech_asr_poll_interval_seconds == 5.0

    def test_asr_poll_max_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SEED_SPEECH_ASR_POLL_MAX_SECONDS", raising=False)
        settings = Settings(_env_file=None)
        assert settings.seed_speech_asr_poll_max_seconds == 600.0

    def test_asr_poll_max_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SEED_SPEECH_ASR_POLL_MAX_SECONDS", "300")
        settings = Settings(_env_file=None)
        assert settings.seed_speech_asr_poll_max_seconds == 300.0

    def test_asr_base_url_rejects_non_https(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SEED_SPEECH_ASR_BASE_URL", "http://voice.example.com")
        with pytest.raises(ValidationError, match="SEED_SPEECH_ASR_BASE_URL must use HTTPS"):
            Settings(_env_file=None)


class TestStateAndArtifactBackend:
    """Settings for the state and durable-artifact backends."""

    def test_object_storage_backend_without_credentials_fails(self) -> None:
        with pytest.raises(
            ValidationError,
            match="ARTIFACT_BACKEND=object_storage requires TOS_\\* or S3_\\* credentials",
        ):
            Settings(_env_file=None, ARTIFACT_BACKEND="object_storage")

    def test_state_backend_accepts_only_sqlite(self) -> None:
        with pytest.raises(ValidationError):
            Settings(_env_file=None, STATE_BACKEND="redis")

    def test_state_backend_sqlite_is_accepted(self) -> None:
        settings = Settings(_env_file=None, STATE_BACKEND="sqlite")
        assert settings.state_backend == "sqlite"

    def test_object_storage_backend_with_tos_credentials_passes(self) -> None:
        settings = Settings(
            _env_file=None,
            ARTIFACT_BACKEND="object_storage",
            TOS_ACCESS_KEY="ak-tos",
            TOS_SECRET_KEY="sk-tos",  # pragma: allowlist secret
            TOS_BUCKET="bucket",
        )
        assert settings.artifact_backend == "object_storage"
        assert settings.has_object_storage is True


class TestEnvFileResolution:
    """``env_file`` is cwd-relative, so an absolute override must be honored."""

    def test_defaults_to_dot_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ARK_MCP_ENV_FILE", raising=False)
        assert resolve_env_file() == ".env"

    def test_absolute_override_is_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARK_MCP_ENV_FILE", "/srv/ark/secrets.env")
        assert resolve_env_file() == "/srv/ark/secrets.env"

    def test_blank_override_falls_back_to_dot_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARK_MCP_ENV_FILE", "   ")
        assert resolve_env_file() == ".env"

    def test_override_loads_settings_from_that_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        env_file = tmp_path / "custom.env"
        env_file.write_text(
            "BYTEPLUS_MODELARK_API_KEY=sk-from-custom-file\n"  # pragma: allowlist secret
        )
        for name in (
            "BYTEPLUS_MODELARK_API_KEY",
            "BYTEPLUS_SEED_SPEECH_API_KEY",
            "BYTEPLUS_VOD_MEDIAKIT_API_KEY",
        ):
            monkeypatch.delenv(name, raising=False)

        settings = Settings(_env_file=str(env_file))

        assert settings.modelark_api_key == "sk-from-custom-file"  # pragma: allowlist secret
        assert settings.has_modelark is True
