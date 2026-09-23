"""Seed understanding adapter — multimodal understanding through ModelArk.

Translates domain input models to provider DTOs, calls the ModelArk Chat
Completions API, and maps provider responses to domain output models.
Forces ``stream: false`` for MVP — SSE streaming is deferred per the plan.
"""

from __future__ import annotations

from typing import Any

import httpx

from ark_mcp.config.env import get_settings
from ark_mcp.observability.logger import debug as log_debug
from ark_mcp.providers.modelark.client import ModelArkGateway
from ark_mcp.providers.modelark.schemas import (
    ChatCompletionProviderRequest,
    ChatCompletionProviderResponse,
    ChatContentPart,
    ChatMessage,
    ChatThinkingConfig,
    ChatUsage,
)
from ark_mcp.providers.modelark.seedance import _parse_success_body


class SeedUnderstandingService:
    """Service layer for Seed 2.1 multimodal understanding."""

    def __init__(self, gateway: ModelArkGateway | None = None) -> None:
        if gateway is None:
            timeout_ms = get_settings().seed_understanding_timeout_ms
            gateway = ModelArkGateway(timeout=timeout_ms / 1000 if timeout_ms else None)
        self._gateway = gateway

    async def generate(
        self,
        request: ChatCompletionProviderRequest,
    ) -> tuple[ChatCompletionProviderResponse, str | None]:
        """Call the ModelArk Chat Completions API.

        Returns the parsed provider response and the ModelArk request ID.
        Raises ``ProviderError`` on non-2xx responses or timeouts.
        """
        log_debug("chat_completion", model=request.model, stream=request.stream)
        try:
            response = await self._gateway.post(
                "/chat/completions", request.model_dump(exclude_none=True)
            )
        except httpx.TimeoutException:
            raise ModelArkGateway.normalize_timeout("chat_completion", side_effect=False) from None
        except httpx.ConnectError as exc:
            raise ModelArkGateway.normalize_connection_error("chat_completion", exc) from exc
        except httpx.TransportError as exc:
            raise ModelArkGateway.normalize_transport_error("chat_completion", exc) from exc

        request_id = ModelArkGateway.extract_request_id(response)

        if response.status_code >= 400:
            raise ModelArkGateway.normalize_error(response, "chat_completion")

        body = _parse_success_body(response, "chat_completion")
        parsed = ChatCompletionProviderResponse.model_validate(body)
        log_debug(
            "chat_completion_complete",
            model=request.model,
            status_code=response.status_code,
            request_id=request_id,
            completion_id=parsed.id,
        )
        return parsed, request_id

    @staticmethod
    def build_request(
        *,
        model: str,
        prompt: str,
        image_parts: list[dict[str, Any]] | None = None,
        video_parts: list[dict[str, Any]] | None = None,
        audio_parts: list[dict[str, Any]] | None = None,
        system: str | None = None,
        thinking: bool = True,
        reasoning_effort: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        repetition_penalty: float | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> ChatCompletionProviderRequest:
        """Build a provider request from domain-level parameters.

        - Translates image/video URL/Base64 inputs into Chat API content parts.
        - Translates audio inputs into ``input_audio`` parts: URLs as
          ``{"url": ...}``; Base64 as raw ``{"data": ..., "format": ...}``, where
          each Base64 part must carry a ``format`` key (e.g. ``wav``, ``mp3``).
        - Forces ``stream: false`` for MVP.
        - ``thinking=True`` (the default) always sends ``thinking.type='enabled'``
          and ``reasoning_effort``; ``thinking=False`` sends neither (used only for
          models whose capabilities report no thinking support).
        - ``response_format`` is passed through verbatim (json_object/json_schema).
        - Video Base64 is rejected (the chat endpoint does not support it).
        """
        content_parts: list[ChatContentPart] = []

        if image_parts:
            for part in image_parts:
                if part.get("kind") == "url":
                    content_parts.append(
                        ChatContentPart(
                            type="image_url",
                            image_url={"url": part["url"]},
                        )
                    )
                elif part.get("kind") == "base64":
                    mime = part.get("mime_type", "image/png")
                    content_parts.append(
                        ChatContentPart(
                            type="image_url",
                            image_url={"url": f"data:{mime};base64,{part['data']}"},
                        )
                    )

        if video_parts:
            for part in video_parts:
                if part.get("kind") == "url":
                    content_parts.append(
                        ChatContentPart(
                            type="video_url",
                            video_url={"url": part["url"]},
                        )
                    )
                elif part.get("kind") == "base64":
                    raise ValueError(
                        "Video Base64 is not supported by the chat endpoint; "
                        "upload via media_upload and pass a URL."
                    )

        if audio_parts:
            for part in audio_parts:
                if part.get("kind") == "url":
                    content_parts.append(
                        ChatContentPart(type="input_audio", input_audio={"url": part["url"]})
                    )
                elif part.get("kind") == "base64":
                    content_parts.append(
                        ChatContentPart(
                            type="input_audio",
                            input_audio={"data": part["data"], "format": part["format"]},
                        )
                    )

        content_parts.append(ChatContentPart(type="text", text=prompt))

        messages: list[ChatMessage] = []
        if system:
            messages.append(ChatMessage(role="system", content=system))
        messages.append(ChatMessage(role="user", content=content_parts))

        thinking_config: ChatThinkingConfig | None = None
        if thinking:
            thinking_config = ChatThinkingConfig(type="enabled")

        return ChatCompletionProviderRequest(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            repetition_penalty=repetition_penalty,
            reasoning_effort=reasoning_effort if thinking else None,
            thinking=thinking_config,
            response_format=response_format,
            stream=False,
        )

    @staticmethod
    def extract_usage(response: ChatCompletionProviderResponse) -> ChatUsage:
        """Extract usage info from the provider response."""
        if response.usage is not None:
            return response.usage
        return ChatUsage()

    @staticmethod
    def extract_completion_id(response: ChatCompletionProviderResponse) -> str | None:
        """Extract the provider completion ID for tracing."""
        return response.id

    async def close(self) -> None:
        await self._gateway.close()
