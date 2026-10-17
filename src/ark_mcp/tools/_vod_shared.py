"""Shared types for VOD AI MediaKit tools."""

from __future__ import annotations

from typing import Literal

from ark_mcp.domain.artifacts import ArtifactPersistenceIssue

VodOperation = Literal[
    "enhance", "transcode", "separate_audio", "add_subtitles", "remove_subtitles"
]

VOD_OPERATION_DESCRIPTION = (
    "MediaKit operation that created the task. Pass it to vod_get_task together with task_id."
)


class VodArtifactPersistenceIssue(ArtifactPersistenceIssue):
    """Safe explanation for a provider success that was not persisted."""
