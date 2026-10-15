"""Provider DTOs for the BytePlus Vision AI OmniHuman 2.0 API."""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AnyUrl, BaseModel, ConfigDict, Field, UrlConstraints, field_validator

SUCCESS_CODE = 10000
CANCEL_NOT_ALLOWED_CODE = 50217
CONTENT_FILTER_CODE = 50510
THROTTLE_CODES: frozenset[int] = frozenset({50429, 50430})
INTERNAL_ERROR_CODES: frozenset[int] = frozenset({50500, 50501})
PROMPT_MAX_LENGTH = 2000

HttpsUrl = Annotated[AnyUrl, UrlConstraints(allowed_schemes=["https"])]

OmniHumanTaskStatus = Literal[
    "queued", "running", "succeeded", "failed", "canceled", "expired", "not_found"
]

PROVIDER_STATUS_MAP: dict[str, OmniHumanTaskStatus] = {
    "in_queue": "queued",
    "generating": "running",
    "done": "succeeded",
    "canceled": "canceled",
    "expired": "expired",
    "not_found": "not_found",
}


class OmniHumanSubmitRequest(BaseModel):
    """Validated OmniHuman 2.0 submission."""

    model_config = ConfigDict(frozen=True)

    image_url: HttpsUrl
    audio_url: HttpsUrl
    prompt: str | None = Field(default=None, max_length=PROMPT_MAX_LENGTH)
    enable_watermark: bool = True

    @field_validator("prompt")
    @classmethod
    def _blank_prompt_is_absent(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    def to_payload(self, req_key: str) -> dict[str, Any]:
        """Return the provider JSON body, omitting an absent prompt."""
        payload: dict[str, Any] = {"req_key": req_key}
        if self.prompt is not None:
            payload["prompt"] = self.prompt
        payload["audio_url"] = str(self.audio_url)
        payload["image_urls"] = [str(self.image_url)]
        payload["enable_watermark"] = self.enable_watermark
        return payload


class CvEnvelope(BaseModel):
    """Common Vision AI response envelope; ``code == 10000`` is the only success signal."""

    model_config = ConfigDict(extra="ignore")

    code: int
    message: str = ""
    request_id: str | None = None
    data: dict[str, Any] | None = None


class OmniHumanSubmission(BaseModel):
    """Accepted OmniHuman task."""

    task_id: str
    request_id: str | None = None


class OmniHumanTaskResult(BaseModel):
    """Normalized OmniHuman task state."""

    status: OmniHumanTaskStatus
    request_id: str | None = None
    video_url: str | None = None
    video_url_expires_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None


class OmniHumanCancelResult(BaseModel):
    """Outcome of a cancel request."""

    canceled: bool
    request_id: str | None = None
    message: str | None = None
