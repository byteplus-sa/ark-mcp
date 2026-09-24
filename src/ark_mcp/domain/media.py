"""Shared media-source input models.

These types are used across tool inputs to describe image, audio, and video
references in a provider-agnostic way. Provider adapters translate them to
the specific provider DTOs.

All user-supplied URLs are validated through the SSRF-safe ``validate_url``
policy, and Base64 data is size-checked via ``check_base64_size`` before
reaching the provider.
"""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar, Literal

from pydantic import BaseModel, Field, model_validator

from ark_mcp.domain.artifacts import MediaType
from ark_mcp.domain.assets import AssetUriError, is_asset_uri, parse_asset_uri
from ark_mcp.observability.logger import warning as log_warning
from ark_mcp.security.media_policy import (
    check_audio_duration_from_base64,
    check_base64_size,
    get_media_limits,
    validate_audio_mime,
    validate_image_mime,
    validate_video_mime,
)
from ark_mcp.security.url_policy import UrlValidationError, validate_url

ASSET_REFERENCE_URL_DESCRIPTION = (
    "HTTPS URL of the media, or 'asset://<asset_id>' for an Active private asset library "
    "asset (ark_asset_create). Required when kind is 'url'."
)


def validate_reference_url(url: str, *, allow_asset: bool, log_event: str) -> None:
    """Validate a media URL, optionally accepting an ``asset://<asset_id>`` reference.

    ``asset://`` never reaches ``validate_url``: it is not fetched by this server,
    so the SSRF policy stays strict for every real URL.
    """
    if is_asset_uri(url):
        if not allow_asset:
            raise ValueError(
                "asset:// references are not supported for this input. They work in "
                "Seedance references (and in Seedream / Seed Audio when "
                "BYTEPLUS_MODELARK_ASSET_REFERENCE_MODE allows it)."
            )
        try:
            parse_asset_uri(url)
        except AssetUriError as exc:
            raise ValueError(str(exc)) from exc
        return
    try:
        validate_url(url)
    except UrlValidationError as exc:
        log_warning(log_event, error=str(exc))
        raise ValueError(UrlValidationError.safe_message) from exc


class MediaSourceKind(StrEnum):
    """Whether a media source is a URL or inline Base64 data."""

    url = "url"
    base64 = "base64"


class MediaSource(BaseModel):
    """A media reference specified by URL or Base64 data.

    Exactly one of ``url`` or ``data`` must be set, matching ``kind``.
    """

    MEDIA_CATEGORY: ClassVar[MediaType] = MediaType.IMAGE
    ALLOW_ASSET_URI: ClassVar[bool] = False

    kind: MediaSourceKind = Field(
        ..., description="Whether the media is referenced by URL or Base64."
    )
    url: str | None = Field(
        default=None,
        description="HTTPS URL of the media. Required when kind is 'url'.",
    )
    data: str | None = Field(
        default=None,
        description="Base64-encoded media data. Required when kind is 'base64'.",
    )
    mime_type: str | None = Field(
        default=None,
        description="MIME type of the media (e.g. image/png, audio/wav).",
    )

    @model_validator(mode="after")
    def validate_source(self) -> MediaSource:
        if self.kind == MediaSourceKind.url and not self.url:
            raise ValueError("url is required when kind is 'url'")
        if self.kind == MediaSourceKind.base64 and not self.data:
            raise ValueError("data is required when kind is 'base64'")
        if self.kind == MediaSourceKind.url and self.data:
            raise ValueError("data must not be set when kind is 'url'")
        if self.kind == MediaSourceKind.base64 and self.url:
            raise ValueError("url must not be set when kind is 'base64'")

        if self.kind == MediaSourceKind.url and self.url:
            validate_reference_url(
                self.url,
                allow_asset=type(self).ALLOW_ASSET_URI,
                log_event="media_source_invalid_url",
            )

        if self.kind == MediaSourceKind.base64 and self.data:
            limits = get_media_limits()
            media_category = type(self).MEDIA_CATEGORY
            max_bytes = {
                "image": limits.image_max_bytes,
                "audio": limits.audio_max_bytes,
                "video": limits.video_max_bytes,
            }[media_category]
            check_base64_size(self.data, max_bytes, label=media_category)

        if self.mime_type:
            media_category = type(self).MEDIA_CATEGORY
            if media_category == "image":
                validate_image_mime(self.mime_type)
            elif media_category == "audio":
                validate_audio_mime(self.mime_type)
            elif media_category == "video":
                validate_video_mime(self.mime_type)

        return self


class ReferenceImageInput(MediaSource):
    """A reference image by URL, Base64 data, or private asset (``asset://<asset_id>``)."""

    MEDIA_CATEGORY: ClassVar[MediaType] = MediaType.IMAGE
    ALLOW_ASSET_URI: ClassVar[bool] = True

    url: str | None = Field(default=None, description=ASSET_REFERENCE_URL_DESCRIPTION)

    @model_validator(mode="before")
    @classmethod
    def accept_media_source(cls, data: object) -> object:
        """Accept a plain ``MediaSource`` instance (pre-asset callers) by re-validating it."""
        if isinstance(data, MediaSource) and not isinstance(data, cls):
            return data.model_dump()
        return data


class AudioReference(BaseModel):
    """An audio reference for Seed Audio, using one of three modes."""

    kind: Literal["speaker", "url", "base64"] = Field(
        ...,
        description="Reference mode: speaker ID, URL, or Base64 data.",
    )
    speaker_id: str | None = Field(
        default=None,
        description="Predefined speaker ID. Required when kind is 'speaker'.",
    )
    url: str | None = Field(
        default=None,
        description=(
            "HTTPS URL of the reference audio, or 'asset://<asset_id>' for an Active private "
            "Audio asset. Required when kind is 'url'."
        ),
    )
    data: str | None = Field(
        default=None,
        description="Base64-encoded reference audio. Required when kind is 'base64'.",
    )
    mime_type: str | None = Field(
        default=None,
        description="MIME type of the reference audio.",
    )

    @model_validator(mode="after")
    def validate_reference(self) -> AudioReference:
        if self.kind == "speaker" and not self.speaker_id:
            raise ValueError("speaker_id is required when kind is 'speaker'")
        if self.kind == "url" and not self.url:
            raise ValueError("url is required when kind is 'url'")
        if self.kind == "base64" and not self.data:
            raise ValueError("data is required when kind is 'base64'")
        if self.kind == "speaker" and (self.url or self.data):
            raise ValueError("url/data must not be set when kind is 'speaker'")
        if self.kind == "url" and (self.speaker_id or self.data):
            raise ValueError("speaker_id/data must not be set when kind is 'url'")
        if self.kind == "base64" and (self.speaker_id or self.url):
            raise ValueError("speaker_id/url must not be set when kind is 'base64'")

        if self.kind == "url" and self.url:
            validate_reference_url(
                self.url, allow_asset=True, log_event="audio_reference_invalid_url"
            )

        if self.kind == "base64" and self.data:
            limits = get_media_limits()
            check_base64_size(self.data, limits.audio_max_bytes, label="audio")
            check_audio_duration_from_base64(self.data, limits.audio_max_seconds, label="audio")

        if self.mime_type:
            validate_audio_mime(self.mime_type)

        return self
