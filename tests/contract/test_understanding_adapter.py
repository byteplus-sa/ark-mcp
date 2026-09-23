"""Contract tests for the Seed 2.1 understanding adapter.

Tests request building (content-part mapping for images/videos, system
messages, thinking config), response parsing, usage extraction, and error
normalization including the billing-safety mutation-set check.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from ark_mcp.domain.errors import ProviderError
from ark_mcp.providers.modelark.client import ModelArkGateway
from ark_mcp.providers.modelark.understanding import SeedUnderstandingService

MODELARK_BASE = "https://ark.ap-southeast.bytepluses.com/api/v3"

# The model strings below are arbitrary adapter inputs passed straight to
# SeedUnderstandingService.build_request/generate. They deliberately do not
# track SEED_UNDERSTANDING_DEFAULT_MODEL, which lives in the settings layer.
TEST_MODEL = "dola-seed-2-1-turbo-260628"


@pytest.fixture
def service() -> SeedUnderstandingService:
    """Create a SeedUnderstandingService with a test gateway."""
    gateway = ModelArkGateway(
        api_key="sk-test-key",  # pragma: allowlist secret
        base_url=MODELARK_BASE,
        timeout=10.0,
        connect_timeout=5.0,
    )
    return SeedUnderstandingService(gateway=gateway)


class TestUnderstandingRequestBuilding:
    """Tests for provider request construction."""

    def test_text_only_prompt(self) -> None:
        request = SeedUnderstandingService.build_request(
            model=TEST_MODEL,
            prompt="What is 2+2?",
        )
        assert request.model == TEST_MODEL
        assert len(request.messages) == 1
        assert request.messages[0].role == "user"
        assert isinstance(request.messages[0].content, list)
        assert len(request.messages[0].content) == 1
        assert request.messages[0].content[0].type == "text"
        assert request.messages[0].content[0].text == "What is 2+2?"
        assert request.stream is False

    def test_image_url_part(self) -> None:
        request = SeedUnderstandingService.build_request(
            model=TEST_MODEL,
            prompt="Describe this image",
            image_parts=[{"kind": "url", "url": "https://cdn.example.com/img.png"}],
        )
        content = request.messages[0].content
        assert isinstance(content, list)
        assert len(content) == 2
        assert content[0].type == "image_url"
        assert content[0].image_url == {"url": "https://cdn.example.com/img.png"}

    def test_image_base64_part(self) -> None:
        request = SeedUnderstandingService.build_request(
            model=TEST_MODEL,
            prompt="Describe this image",
            image_parts=[
                {"kind": "base64", "data": "aV9hbV9hbl9pbWFnZQ==", "mime_type": "image/png"}
            ],
        )
        content = request.messages[0].content
        assert isinstance(content, list)
        assert content[0].type == "image_url"
        assert content[0].image_url == {"url": "data:image/png;base64,aV9hbV9hbl9pbWFnZQ=="}

    def test_video_url_part(self) -> None:
        request = SeedUnderstandingService.build_request(
            model=TEST_MODEL,
            prompt="What happens in this video?",
            video_parts=[{"kind": "url", "url": "https://cdn.example.com/video.mp4"}],
        )
        content = request.messages[0].content
        assert isinstance(content, list)
        assert content[0].type == "video_url"
        assert content[0].video_url == {"url": "https://cdn.example.com/video.mp4"}

    def test_video_base64_rejected(self) -> None:
        with pytest.raises(ValueError, match="Video Base64 is not supported"):
            SeedUnderstandingService.build_request(
                model=TEST_MODEL,
                prompt="describe video",
                video_parts=[{"kind": "base64", "data": "dGVzdA=="}],
            )

    def test_system_message_prepended(self) -> None:
        request = SeedUnderstandingService.build_request(
            model=TEST_MODEL,
            prompt="Hello",
            system="You are a helpful assistant.",
        )
        assert len(request.messages) == 2
        assert request.messages[0].role == "system"
        assert request.messages[0].content == "You are a helpful assistant."
        assert request.messages[1].role == "user"

    def test_thinking_enabled(self) -> None:
        request = SeedUnderstandingService.build_request(
            model=TEST_MODEL,
            prompt="Solve this puzzle",
            thinking=True,
        )
        assert request.thinking is not None
        assert request.thinking.type == "enabled"

    def test_thinking_enabled_by_default(self) -> None:
        request = SeedUnderstandingService.build_request(
            model=TEST_MODEL,
            prompt="Hello",
        )
        assert request.thinking is not None
        assert request.thinking.type == "enabled"

    def test_thinking_false_sends_no_thinking_fields(self) -> None:
        """Only used for bindings without thinking support."""
        request = SeedUnderstandingService.build_request(
            model=TEST_MODEL,
            prompt="Hello",
            thinking=False,
            reasoning_effort="medium",
        )
        assert request.thinking is None
        assert request.reasoning_effort is None

    def test_response_format_passthrough(self) -> None:
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": "beats", "schema": {"type": "object"}, "strict": True},
        }
        request = SeedUnderstandingService.build_request(
            model=TEST_MODEL, prompt="Hello", response_format=response_format
        )
        body = request.model_dump(exclude_none=True)
        assert body["response_format"] == response_format

    def test_reasoning_effort_only_with_thinking(self) -> None:
        request = SeedUnderstandingService.build_request(
            model=TEST_MODEL,
            prompt="Solve this",
            thinking=True,
            reasoning_effort="medium",
        )
        assert request.reasoning_effort == "medium"

    def test_reasoning_effort_none_without_thinking(self) -> None:
        request = SeedUnderstandingService.build_request(
            model=TEST_MODEL,
            prompt="Hello",
            thinking=False,
            reasoning_effort="high",
        )
        assert request.reasoning_effort is None

    def test_combined_images_and_videos(self) -> None:
        request = SeedUnderstandingService.build_request(
            model=TEST_MODEL,
            prompt="Compare the image and the video",
            image_parts=[{"kind": "url", "url": "https://cdn.example.com/img.png"}],
            video_parts=[{"kind": "url", "url": "https://cdn.example.com/vid.mp4"}],
        )
        content = request.messages[0].content
        assert isinstance(content, list)
        assert len(content) == 3
        assert content[0].type == "image_url"
        assert content[1].type == "video_url"
        assert content[2].type == "text"

    def test_generation_params_propagated(self) -> None:
        request = SeedUnderstandingService.build_request(
            model=TEST_MODEL,
            prompt="Hello",
            temperature=0.5,
            max_tokens=2048,
            top_p=0.9,
            repetition_penalty=1.1,
        )
        assert request.temperature == 0.5
        assert request.max_tokens == 2048
        assert request.top_p == 0.9
        assert request.repetition_penalty == 1.1

    def test_stream_always_false(self) -> None:
        request = SeedUnderstandingService.build_request(
            model=TEST_MODEL,
            prompt="test",
        )
        assert request.stream is False


class TestUnderstandingResponseParsing:
    """Tests for provider response parsing."""

    @respx.mock
    async def test_basic_response(self, service: SeedUnderstandingService) -> None:
        respx.post(f"{MODELARK_BASE}/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "chatcmpl-123",
                    "model": TEST_MODEL,
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "The image shows a cat.",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
                },
                headers={"X-Request-Id": "req-1"},
            )
        )
        request = SeedUnderstandingService.build_request(model=TEST_MODEL, prompt="describe")
        response, request_id = await service.generate(request)
        assert request_id == "req-1"
        assert response.id == "chatcmpl-123"
        assert len(response.choices) == 1
        assert response.choices[0].message.content == "The image shows a cat."
        assert response.choices[0].finish_reason == "stop"

    @respx.mock
    async def test_reasoning_content_present(self, service: SeedUnderstandingService) -> None:
        respx.post(f"{MODELARK_BASE}/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "chatcmpl-456",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "The answer is 4.",
                                "reasoning_content": "First, I observe that 2+2...",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 15, "total_tokens": 20},
                },
            )
        )
        request = SeedUnderstandingService.build_request(
            model=TEST_MODEL, prompt="What is 2+2?", thinking=True
        )
        response, _ = await service.generate(request)
        assert response.choices[0].message.reasoning_content == "First, I observe that 2+2..."

    @respx.mock
    async def test_reasoning_content_absent_without_thinking(
        self, service: SeedUnderstandingService
    ) -> None:
        respx.post(f"{MODELARK_BASE}/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "chatcmpl-789",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "Hello!"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
                },
            )
        )
        request = SeedUnderstandingService.build_request(model=TEST_MODEL, prompt="Hi")
        response, _ = await service.generate(request)
        assert response.choices[0].message.reasoning_content is None

    @respx.mock
    async def test_usage_extraction(self, service: SeedUnderstandingService) -> None:
        respx.post(f"{MODELARK_BASE}/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "chatcmpl-usage",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "Done."},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
                },
            )
        )
        request = SeedUnderstandingService.build_request(model=TEST_MODEL, prompt="test")
        response, _ = await service.generate(request)
        usage = SeedUnderstandingService.extract_usage(response)
        assert usage.prompt_tokens == 100
        assert usage.completion_tokens == 200
        assert usage.total_tokens == 300

    @respx.mock
    async def test_completion_id_extraction(self, service: SeedUnderstandingService) -> None:
        respx.post(f"{MODELARK_BASE}/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "chatcmpl-trace-123",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "Done."},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
            )
        )
        request = SeedUnderstandingService.build_request(model=TEST_MODEL, prompt="test")
        response, _ = await service.generate(request)
        assert SeedUnderstandingService.extract_completion_id(response) == "chatcmpl-trace-123"


class TestUnderstandingErrorPropagation:
    """Tests for error propagation and billing safety."""

    @respx.mock
    async def test_provider_error_raised(self, service: SeedUnderstandingService) -> None:
        respx.post(f"{MODELARK_BASE}/chat/completions").mock(
            return_value=httpx.Response(
                400,
                json={"error": {"code": "INVALID_PARAM", "message": "bad model"}},
            )
        )
        request = SeedUnderstandingService.build_request(model="bad-model", prompt="test")
        with pytest.raises(ProviderError) as exc_info:
            await service.generate(request)
        assert exc_info.value.http_status == 400
        assert exc_info.value.code == "INVALID_PARAM"

    @respx.mock
    async def test_500_is_ambiguous_completion(self, service: SeedUnderstandingService) -> None:
        """5xx on chat_completion must be ambiguous (blocks retry, prevents double-billing)."""
        respx.post(f"{MODELARK_BASE}/chat/completions").mock(
            return_value=httpx.Response(
                500,
                json={"error": {"code": "INTERNAL", "message": "server error"}},
            )
        )
        request = SeedUnderstandingService.build_request(model=TEST_MODEL, prompt="test")
        with pytest.raises(ProviderError) as exc_info:
            await service.generate(request)
        assert exc_info.value.http_status == 500
        assert exc_info.value.retryable is True
        assert exc_info.value.ambiguous_completion is True

    @respx.mock
    async def test_401_not_ambiguous(self, service: SeedUnderstandingService) -> None:
        respx.post(f"{MODELARK_BASE}/chat/completions").mock(
            return_value=httpx.Response(
                401,
                json={"error": {"code": "UNAUTHORIZED", "message": "bad key"}},
            )
        )
        request = SeedUnderstandingService.build_request(model=TEST_MODEL, prompt="test")
        with pytest.raises(ProviderError) as exc_info:
            await service.generate(request)
        assert exc_info.value.http_status == 401
        assert exc_info.value.ambiguous_completion is False

    @respx.mock
    async def test_timeout_is_retryable_not_ambiguous(
        self, service: SeedUnderstandingService
    ) -> None:
        respx.post(f"{MODELARK_BASE}/chat/completions").mock(
            side_effect=httpx.TimeoutException("timed out")
        )
        request = SeedUnderstandingService.build_request(model=TEST_MODEL, prompt="test")
        with pytest.raises(ProviderError) as exc_info:
            await service.generate(request)
        assert exc_info.value.code == "TIMEOUT"
        # A chat completion creates no provider state, so a timeout is safe to retry.
        assert exc_info.value.ambiguous_completion is False
        assert exc_info.value.retryable is True

    @respx.mock
    async def test_connection_error_raises(self, service: SeedUnderstandingService) -> None:
        respx.post(f"{MODELARK_BASE}/chat/completions").mock(
            side_effect=httpx.ConnectError("refused")
        )
        request = SeedUnderstandingService.build_request(model=TEST_MODEL, prompt="test")
        with pytest.raises(ProviderError) as exc_info:
            await service.generate(request)
        assert exc_info.value.code == "CONNECTION_ERROR"

    @respx.mock
    async def test_non_json_success_body_raises_invalid_response(
        self, service: SeedUnderstandingService
    ) -> None:
        respx.post(f"{MODELARK_BASE}/chat/completions").mock(
            return_value=httpx.Response(200, content=b"")
        )
        request = SeedUnderstandingService.build_request(model=TEST_MODEL, prompt="test")
        with pytest.raises(ProviderError) as exc_info:
            await service.generate(request)
        assert exc_info.value.code == "INVALID_RESPONSE"
        assert exc_info.value.http_status == 200

    @respx.mock
    async def test_transport_error_raises(self, service: SeedUnderstandingService) -> None:
        respx.post(f"{MODELARK_BASE}/chat/completions").mock(
            side_effect=httpx.ReadError("read failed")
        )
        request = SeedUnderstandingService.build_request(model=TEST_MODEL, prompt="test")
        with pytest.raises(ProviderError) as exc_info:
            await service.generate(request)
        assert exc_info.value.code == "TRANSPORT_ERROR"

    @respx.mock
    async def test_usage_none_fallback_returns_zeros(
        self, service: SeedUnderstandingService
    ) -> None:
        respx.post(f"{MODELARK_BASE}/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "chatcmpl-no-usage",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                },
            )
        )
        request = SeedUnderstandingService.build_request(model=TEST_MODEL, prompt="test")
        response, _ = await service.generate(request)
        usage = SeedUnderstandingService.extract_usage(response)
        assert usage.prompt_tokens == 0
        assert usage.completion_tokens == 0
        assert usage.total_tokens == 0

    @respx.mock
    async def test_empty_choices_list(self, service: SeedUnderstandingService) -> None:
        respx.post(f"{MODELARK_BASE}/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "chatcmpl-empty",
                    "choices": [],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
                },
            )
        )
        request = SeedUnderstandingService.build_request(model=TEST_MODEL, prompt="test")
        response, _ = await service.generate(request)
        assert len(response.choices) == 0


class TestUnderstandingModelFamilyAutoDetection:
    """Settings-layer tests verifying default-model family auto-detection.

    The code default for ``seed_understanding_default_model`` must resolve to
    TURBO, and the recognized Pro-tier built-in ID ``dola-seed-evolving`` must
    resolve to PRO without requiring an explicit
    ``SEED_UNDERSTANDING_MODEL_FAMILY``. Any other custom ID must raise
    ``ValueError`` when neither family nor bindings are supplied.
    """

    def test_default_model_resolves_to_turbo(self) -> None:
        from ark_mcp.config.env import Settings

        settings = Settings(_env_file=None)
        assert settings.seed_understanding_default_model == "dola-seed-2-1-turbo-260628"
        assert len(settings.seed_understanding_model_bindings) == 1
        binding = settings.seed_understanding_model_bindings[0]
        assert binding.model_id == "dola-seed-2-1-turbo-260628"
        assert binding.family.value == "turbo"

    def test_dola_seed_evolving_auto_resolves_to_pro(self) -> None:
        from ark_mcp.config.env import Settings

        settings = Settings(
            _env_file=None,
            SEED_UNDERSTANDING_DEFAULT_MODEL="dola-seed-evolving",
        )
        assert settings.seed_understanding_default_model == "dola-seed-evolving"
        assert len(settings.seed_understanding_model_bindings) == 1
        binding = settings.seed_understanding_model_bindings[0]
        assert binding.model_id == "dola-seed-evolving"
        assert binding.family.value == "pro"

    def test_unknown_custom_id_without_family_raises(self) -> None:
        from pydantic import ValidationError

        from ark_mcp.config.env import Settings

        with pytest.raises((ValueError, ValidationError)):
            Settings(
                _env_file=None,
                SEED_UNDERSTANDING_DEFAULT_MODEL="my-custom-model",
            )
