"""Seedream adapter — image generation and editing through ModelArk.

Translates domain input models to provider DTOs, calls the ModelArk gateway,
and maps provider responses to domain output models. Forces ``stream: false``
for MVP — streaming events are deferred per the plan.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import httpx

from ark_mcp.domain.models import SeedreamItemError, SeedreamUsage
from ark_mcp.observability.logger import debug as log_debug
from ark_mcp.providers.modelark.client import ModelArkGateway
from ark_mcp.providers.modelark.schemas import (
    SeedreamProviderRequest,
    SeedreamProviderResponse,
)
from ark_mcp.providers.modelark.seedance import _parse_success_body


def _image_value(item: dict[str, Any]) -> str | None:
    """Resolve one reference image to a URL or base64 data URI."""
    if item.get("url"):
        return cast("str", item["url"])
    if item.get("data"):
        mime = item.get("mime_type", "image/png")
        return f"data:{mime};base64,{item['data']}"
    return None


class SeedreamService:
    """Service layer for Seedream image generation."""

    def __init__(self, gateway: ModelArkGateway | None = None) -> None:
        self._gateway = gateway or ModelArkGateway()

    async def generate(
        self,
        request: SeedreamProviderRequest,
    ) -> tuple[SeedreamProviderResponse, str | None]:
        """Call the ModelArk image generation API.

        Returns the parsed provider response and the ModelArk request ID.
        Raises ``NormalizedProviderError`` on non-2xx responses or timeouts.
        """
        log_debug("seedream_generate", model=request.model, stream=request.stream)
        try:
            response = await self._gateway.post(
                "/images/generations", request.model_dump(exclude_none=True)
            )
        except httpx.TimeoutException:
            raise ModelArkGateway.normalize_timeout("generate_image") from None
        except httpx.ConnectError as exc:
            raise ModelArkGateway.normalize_connection_error("generate_image", exc) from exc
        except httpx.TransportError as exc:
            raise ModelArkGateway.normalize_transport_error("generate_image", exc) from exc

        request_id = ModelArkGateway.extract_request_id(response)

        if response.status_code >= 400:
            raise ModelArkGateway.normalize_error(response, "generate_image")

        body = _parse_success_body(response, "generate_image")
        parsed = SeedreamProviderResponse.model_validate(body)
        log_debug(
            "seedream_generate_complete",
            model=request.model,
            status_code=response.status_code,
            request_id=request_id,
            items=len(parsed.data) if parsed.data else 0,
        )
        return parsed, request_id

    @staticmethod
    def build_request(
        *,
        model: str,
        prompt: str,
        images: list[dict[str, Any]] | None = None,
        size: str | None = None,
        seed: int | None = None,
        max_images: int | None = None,
        output_format: str | None = None,
        response_format: str | None = None,
        watermark: bool | None = None,
        prompt_optimization: str | None = None,
        background: str | None = None,
    ) -> SeedreamProviderRequest:
        """Build a provider request from domain-level parameters.

        - Derives ``sequential_image_generation`` from ``max_images``.
        - Forces ``stream: false`` for MVP.
        """
        image_field: str | list[str] | None = None
        if images:
            if len(images) == 1:
                image_field = _image_value(images[0])
            else:
                image_field = [
                    value for value in (_image_value(item) for item in images) if value is not None
                ]

        sequential = None
        sequential_options = None
        if max_images is not None and max_images > 1:
            sequential = "auto"
            sequential_options = {"max_images": max_images}

        optimize_options = None
        if prompt_optimization:
            optimize_options = {"mode": prompt_optimization}

        return SeedreamProviderRequest(
            model=model,
            prompt=prompt,
            image=image_field,
            size=size,
            seed=seed,
            sequential_image_generation=sequential,
            sequential_image_generation_options=sequential_options,
            stream=False,
            output_format=output_format,
            response_format=response_format,
            watermark=watermark,
            optimize_prompt_options=optimize_options,
            background=background,
        )

    @staticmethod
    def extract_usage(
        response: SeedreamProviderResponse,
    ) -> SeedreamUsage:
        """Extract usage info from the provider response."""
        usage = response.usage or {}
        return SeedreamUsage(
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
        )

    @staticmethod
    def extract_item_errors(
        response: SeedreamProviderResponse,
    ) -> list[SeedreamItemError]:
        """Extract per-item errors from a Seedream batch response.

        The provider may include partial failures in the ``data`` array.
        """
        errors: list[SeedreamItemError] = []
        for item in response.data:
            if item.b64_json is None and item.url is None:
                errors.append(
                    SeedreamItemError(
                        index=item.index if item.index is not None else 0,
                        code="NO_OUTPUT",
                        message="Image generation produced no output.",
                    )
                )
        return errors

    @staticmethod
    def get_created_at(response: SeedreamProviderResponse) -> str:
        """Format the ``created`` timestamp as ISO-8601."""
        if response.created is not None:
            return datetime.fromtimestamp(response.created, tz=UTC).isoformat()
        return datetime.now(UTC).isoformat()

    async def close(self) -> None:
        await self._gateway.close()
