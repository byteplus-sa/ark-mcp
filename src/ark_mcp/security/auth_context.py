"""Auth context for multi-tenant artifact and task ownership.

In ``stdio`` mode, there is a single principal (the local user). In remote
HTTP mode, the shared-key verifier supplies the team identity and scopes.
This module provides the ownership abstraction for both.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PrincipalContext(BaseModel):
    """Principal identity and tenant for ownership checks."""

    model_config = ConfigDict(frozen=True)

    principal_id: str = "local"
    tenant_id: str = "local"
    scopes: frozenset[str] = Field(default_factory=frozenset)
    transport: Literal["stdio", "http"] = "stdio"

    @property
    def is_local(self) -> bool:
        return self.principal_id == "local" and self.transport == "stdio"


# Compatibility alias while persistence protocols migrate terminology.
AuthContext = PrincipalContext
