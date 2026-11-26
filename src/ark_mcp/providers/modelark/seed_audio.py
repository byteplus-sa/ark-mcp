"""Synchronous Seed Audio 1.5 generation via ModelArk."""

from __future__ import annotations

from typing import Annotated, Any, Literal

import httpx
from pydantic import AfterValidator, BaseModel, Field

from ark_mcp.domain.errors import NormalizedProviderError, ProviderError
from ark_mcp.providers.modelark.client import ModelArkGateway
from ark_mcp.security.url_policy import UrlValidationError, validate_url_syntax

_OPERATION = "generate_audio"


def _validate_output_url(url: str) -> str:
    try:
        validate_url_syntax(url)
    except UrlValidationError as exc:
        raise ValueError("Expected an HTTPS provider output URL.") from exc
    return url


ProviderOutputUrl = Annotated[str, AfterValidator(_validate_output_url)]


class SeedAudioTrackData(BaseModel):
    url: ProviderOutputUrl
    type: str | None = None
    description: str | None = None


class SeedAudioSubtitleData(BaseModel):
    format: Literal["srt", "jsonl"]
    url: ProviderOutputUrl


class SeedAudioContent(BaseModel):
    audios: list[SeedAudioTrackData] = Field(min_length=1)
    video_url: ProviderOutputUrl | None = None
    subtitles: list[SeedAudioSubtitleData] = Field(default_factory=list)
    subtitles_source: list[SeedAudioSubtitleData] = Field(default_factory=list)


class SeedAudioUsage(BaseModel):
    duration_ms: int | None = Field(None, ge=0)
    final_duration_ms: int | None = Field(None, ge=0)


class SeedAudioModelArkResponse(BaseModel):
    id: str = Field(min_length=1)
    content: SeedAudioContent
    output_format: Literal["wav", "mp3", "pcm", "ogg_opus"] | None = None
    usage: SeedAudioUsage = Field(
        default_factory=lambda: SeedAudioUsage(duration_ms=None, final_duration_ms=None)
    )


class SeedAudioModelArkService:
    """Submit a billed synchronous generation and validate its response."""

    def __init__(self, gateway: ModelArkGateway | None = None) -> None:
        self._gateway = gateway or ModelArkGateway()

    async def generate(
        self, request: dict[str, Any]
    ) -> tuple[SeedAudioModelArkResponse, str | None]:
        try:
            response = await self._gateway.post("/contents/generations", request)
        except (httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout) as exc:
            raise ModelArkGateway.normalize_connection_error(_OPERATION, exc) from exc
        except httpx.TimeoutException:
            raise ModelArkGateway.normalize_timeout(_OPERATION) from None
        except httpx.TransportError as exc:
            raise ProviderError(
                NormalizedProviderError(
                    provider="modelark",
                    operation=_OPERATION,
                    code="TRANSPORT_ERROR",
                    message="Connection interrupted during Seed Audio generation. Check before retrying.",
                    retryable=False,
                    ambiguous_completion=True,
                )
            ) from exc

        request_id = ModelArkGateway.extract_request_id(response)
        if not response.is_success:
            raise ModelArkGateway.normalize_error(response, _OPERATION)

        try:
            payload = response.json()
            if isinstance(payload, dict) and payload.get("error"):
                raise ModelArkGateway.normalize_error(response, _OPERATION)
            return SeedAudioModelArkResponse.model_validate(payload), request_id
        except ValueError:
            raise ProviderError(
                NormalizedProviderError(
                    provider="modelark",
                    operation=_OPERATION,
                    code="INVALID_RESPONSE",
                    http_status=response.status_code,
                    message="Seed Audio returned an invalid generation response.",
                    request_id=request_id,
                    retryable=False,
                    ambiguous_completion=True,
                )
            ) from None

    async def close(self) -> None:
        await self._gateway.close()
