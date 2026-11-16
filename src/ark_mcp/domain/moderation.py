"""Moderation lookup types from specs/SPEC_MODELARK_MODERATION_CONTRACT.md."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ModerationIdType = Literal["task_id", "asset_id", "request_id"]


class ModerationBlockReason(BaseModel):
    """One provider-reported classification, not an independent safety finding."""

    model_config = ConfigDict(strict=True)

    label: str = Field(
        ...,
        min_length=1,
        description="Provider category: Safety, Copyright, Celebrity, Deepfake, or a new label.",
    )
    sub_label: str = Field(
        ..., description="Provider subcategory, preserved without interpretation."
    )
    detail: str | None = Field(
        None, description="Provider explanation when available; may be absent, null, or empty."
    )


class ModerationResult(BaseModel):
    """Block reasons for one existing provider record."""

    id: str = Field(
        ..., description="The queried provider ID, not the lookup's diagnostic request ID."
    )
    type: ModerationIdType = Field(
        ..., description="Whether id identifies a task, asset, or request."
    )
    block_reasons: list[ModerationBlockReason] = Field(
        ...,
        description="Provider-reported block reasons. An empty list does not establish approval or safety.",
    )
