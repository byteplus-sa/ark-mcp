"""Shared output types for VOD AI MediaKit tools."""

from __future__ import annotations

from ark_mcp.domain.artifacts import ArtifactPersistenceIssue


class VodArtifactPersistenceIssue(ArtifactPersistenceIssue):
    """Safe explanation for a provider success that was not persisted."""
