"""Integration tests for the ``seed_understand`` tool handler.

Exercises the full path: input validation → capability registry check →
provider call (mocked) → structured output. Covers text-only, image
understanding, video understanding, reasoning mode, and error paths.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp.tools import ToolResult

from ark_mcp.domain.errors import NormalizedProviderError, ProviderError
from ark_mcp.providers.modelark.schemas import ChatCompletionProviderResponse
from ark_mcp.providers.modelark.understanding import SeedUnderstandingService
from ark_mcp.tools.seed_understand import (
    SeedUnderstandInput,
    SeedUnderstandOutput,
    seed_understand,
)
from tests.fixtures.fake_context import FakeContext


def _patch_understanding_service(
    monkeypatch: pytest.MonkeyPatch, response_data: dict[str, Any]
) -> None:
    """Patch SeedUnderstandingService.generate to return a fixed response."""

    async def mock_generate(
        self: SeedUnderstandingService, request: Any
    ) -> tuple[ChatCompletionProviderResponse, str | None]:
        response = ChatCompletionProviderResponse.model_validate(response_data)
        return response, "req-test-789"

    monkeypatch.setattr(SeedUnderstandingService, "generate", mock_generate)


class TestSeedUnderstandTool:
    """Full-path integration tests for seed_understand."""

    async def test_text_only_understanding(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_understanding_service(
            monkeypatch,
            {
                "id": "chatcmpl-001",
                "model": "dola-seed-2-1-turbo-260628",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "4"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
            },
        )

        result = await seed_understand(SeedUnderstandInput(prompt="What is 2+2?"), fake_ctx)

        assert isinstance(result, SeedUnderstandOutput)
        assert result.provider == "byteplus-modelark"
        assert result.model == "dola-seed-2-1-turbo-260628"
        assert result.completion_id == "chatcmpl-001"
        assert result.request_id == "req-test-789"
        assert len(result.choices) == 1
        assert result.choices[0].content == "4"
        assert result.choices[0].finish_reason == "stop"
        assert "reasoning_content" not in result.choices[0].model_dump()
        assert result.choices[0].content_chars == 1
        assert result.usage.prompt_tokens == 5
        assert result.usage.completion_tokens == 1
        assert result.usage.total_tokens == 6

    async def test_image_understanding(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_understanding_service(
            monkeypatch,
            {
                "id": "chatcmpl-002",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "A red circle."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60},
            },
        )

        result = await seed_understand(
            SeedUnderstandInput(
                prompt="What is in this image?",
                images=[
                    {
                        "kind": "url",
                        "url": "https://cdn.example.com/image.png",
                        "mime_type": "image/png",
                    }
                ],
            ),
            fake_ctx,
        )

        assert isinstance(result, SeedUnderstandOutput)
        assert result.choices[0].content == "A red circle."

    async def test_video_understanding(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_understanding_service(
            monkeypatch,
            {
                "id": "chatcmpl-003",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "A person is walking down the street.",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 200, "completion_tokens": 20, "total_tokens": 220},
            },
        )

        result = await seed_understand(
            SeedUnderstandInput(
                prompt="Describe what happens in this video",
                videos=[
                    {
                        "kind": "url",
                        "url": "https://cdn.example.com/video.mp4",
                        "mime_type": "video/mp4",
                    }
                ],
            ),
            fake_ctx,
        )

        assert isinstance(result, SeedUnderstandOutput)
        assert "walking" in result.choices[0].content
        assert result.usage.total_tokens == 220

    async def test_reasoning_is_never_returned(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_understanding_service(
            monkeypatch,
            {
                "id": "chatcmpl-004",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "The answer is 42.",
                            "reasoning_content": "First, I consider the question...",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 30, "total_tokens": 40},
            },
        )

        result = await seed_understand(
            SeedUnderstandInput(prompt="What is the meaning of life?"),
            fake_ctx,
        )

        assert isinstance(result, SeedUnderstandOutput)
        assert result.choices[0].content == "The answer is 42."
        assert "First, I consider" not in result.model_dump_json()

    async def test_video_base64_rejected_at_input(
        self,
        test_env: None,
        fake_ctx: FakeContext,
    ) -> None:
        with pytest.raises(ValueError, match="Video Base64 is not supported"):
            await seed_understand(
                SeedUnderstandInput(
                    prompt="describe video",
                    videos=[{"kind": "base64", "data": "dGVzdA==", "mime_type": "video/mp4"}],
                ),
                fake_ctx,
            )

    async def test_provider_error_returns_tool_result(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def mock_generate(
            self: SeedUnderstandingService, request: Any
        ) -> tuple[ChatCompletionProviderResponse, str | None]:
            raise ProviderError(
                NormalizedProviderError(
                    provider="modelark",
                    operation="chat_completion",
                    http_status=429,
                    code="RATE_LIMITED",
                    message="rate limit exceeded",
                    retryable=True,
                    ambiguous_completion=False,
                )
            )

        monkeypatch.setattr(SeedUnderstandingService, "generate", mock_generate)

        result = await seed_understand(SeedUnderstandInput(prompt="test"), fake_ctx)

        assert isinstance(result, ToolResult)
        assert result.is_error
        assert result.structured_content is None
        assert "http_status=429" in result.content[0].text

    async def test_unsupported_model_rejected(
        self,
        test_env: None,
        fake_ctx: FakeContext,
    ) -> None:
        with pytest.raises(
            ValueError, match="not in the configured understanding capability registry"
        ):
            await seed_understand(
                SeedUnderstandInput(prompt="test", model="nonexistent-model-id"),
                fake_ctx,
            )

    async def test_too_many_media_parts_rejected(
        self,
        test_env: None,
        fake_ctx: FakeContext,
    ) -> None:
        from ark_mcp.domain.media import MediaSourceKind

        images = [
            {
                "kind": MediaSourceKind.url,
                "url": f"https://cdn.example.com/{i}.png",
                "mime_type": "image/png",
            }
            for i in range(33)
        ]
        with pytest.raises(ValueError, match="at most 32"):
            await seed_understand(SeedUnderstandInput(prompt="test", images=images), fake_ctx)

    async def test_thinking_false_is_ignored_and_effort_defaults_to_medium(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Deep thinking is always on: thinking=false is a deprecated no-op."""
        captured_request: list[Any] = []

        async def mock_generate(
            self: SeedUnderstandingService, request: Any
        ) -> tuple[ChatCompletionProviderResponse, str | None]:
            captured_request.append(request)
            response = ChatCompletionProviderResponse.model_validate(
                {
                    "id": "chatcmpl-005",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            )
            return response, "req-test"

        monkeypatch.setattr(SeedUnderstandingService, "generate", mock_generate)

        result = await seed_understand(
            SeedUnderstandInput(prompt="test", thinking=False),
            fake_ctx,
        )

        assert isinstance(result, SeedUnderstandOutput)
        assert captured_request[0].thinking.type == "enabled"
        assert captured_request[0].reasoning_effort == "medium"
