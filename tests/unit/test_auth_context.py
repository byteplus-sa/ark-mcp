"""Unit tests for principal/auth context locality rules."""

from __future__ import annotations

from ark_mcp.security.auth_context import PrincipalContext


class TestPrincipalContextIsLocal:
    def test_local_principal_over_stdio_is_local(self) -> None:
        assert PrincipalContext(principal_id="local", transport="stdio").is_local is True

    def test_local_principal_over_http_is_not_local(self) -> None:
        assert PrincipalContext(principal_id="local", transport="http").is_local is False

    def test_spoofed_remote_principal_is_not_local(self) -> None:
        spoofed = PrincipalContext(
            principal_id="local",
            tenant_id="tenant-a",
            transport="http",
            scopes=frozenset({"seedance:read"}),
        )
        assert spoofed.is_local is False
