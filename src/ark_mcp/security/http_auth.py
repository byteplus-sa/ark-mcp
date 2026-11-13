"""FastMCP HTTP authentication and component-scope policy."""

from __future__ import annotations

import hashlib
import hmac
import re

from fastmcp.server.auth import (
    AccessToken,
    AuthCheck,
    AuthProvider,
    TokenVerifier,
    require_scopes,
)

from ark_mcp.config.env import AuthMode, Settings
from ark_mcp.observability.logger import info as log_info

SHARED_TEAM_SCOPES = (
    "seed:audio:generate",
    "seedream:generate",
    "seedance:create",
    "gen:read",
    "gen:delete",
    "seed:asr:transcribe",
    "understanding:read",
    "hyper3d:create",
    "hitem3d:create",
    "omnihuman:create",
    "omnihuman:read",
    "omnihuman:cancel",
    "vod:enhance",
    "vod:transcode",
    "vod:subtitle:add",
    "vod:subtitle:remove",
    "vod:read",
    "vod:extract",
    "media:upload",
    "media:presign",
    "artifacts:read",
)


class SharedKeyVerifier(TokenVerifier):
    """Validate one hashed bearer key and assign a stable, non-admin team identity."""

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._digest = settings.mcp_api_key_sha256.get_secret_value()

    async def verify_token(self, token: str) -> AccessToken | None:
        if len(token) != 64 or not re.fullmatch(r"[a-f0-9]{64}", token):
            return None
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        if not hmac.compare_digest(digest, self._digest):
            return None
        return AccessToken(
            token=token,
            client_id="ark-mcp-shared-key",
            subject="shared-team",
            scopes=list(SHARED_TEAM_SCOPES),
            claims={"sub": "shared-team", "tenant_id": "shared-team"},
        )


def build_auth_provider(settings: Settings) -> AuthProvider | None:
    """Build the shared-key verifier; local stdio/loopback mode uses none."""
    if settings.mcp_auth_mode is AuthMode.LOCAL:
        log_info("auth_mode", mode="local")
        return None
    log_info("auth_mode", mode="api_key")
    return SharedKeyVerifier(settings)


def component_auth(settings: Settings, *scopes: str) -> AuthCheck | None:
    """Require scopes only when token authentication is active."""
    if settings.mcp_auth_mode is AuthMode.LOCAL:
        return None
    return require_scopes(*scopes)
