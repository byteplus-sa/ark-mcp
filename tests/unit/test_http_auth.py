"""Authentication provider selection for local and shared-key modes."""

from __future__ import annotations

from ark_mcp.config.env import Settings
from ark_mcp.security.http_auth import SharedKeyVerifier, build_auth_provider, component_auth


class TestBuildAuthProvider:
    def test_local_mode_returns_none(self) -> None:
        settings = Settings(_env_file=None, MCP_AUTH_MODE="local")
        assert build_auth_provider(settings) is None

    def test_api_key_mode_returns_shared_verifier_without_auth_routes(self) -> None:
        settings = Settings(
            _env_file=None,
            MCP_AUTH_MODE="api_key",
            MCP_API_KEY_SHA256="a" * 64,
        )
        provider = build_auth_provider(settings)
        assert isinstance(provider, SharedKeyVerifier)
        assert provider.get_routes(mcp_path="/mcp") == []
        assert component_auth(settings, "artifacts:read") is not None

    def test_local_mode_does_not_apply_scope_checks(self) -> None:
        assert component_auth(Settings(_env_file=None), "artifacts:read") is None
