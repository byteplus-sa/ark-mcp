"""Contract tests for the BytePlus Vision AI OmniHuman 2.0 adapter.

All responses are sanitized fixtures. No real provider request is made.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from pydantic import ValidationError

from ark_mcp.domain.errors import ProviderError
from ark_mcp.providers.vision_cv.client import VisionCvGateway
from ark_mcp.providers.vision_cv.omnihuman import OmniHumanService
from ark_mcp.providers.vision_cv.schemas import OmniHumanSubmitRequest

BASE_URL = "https://openapi.cv.example.com"
SUBMIT = f"{BASE_URL}/api/common/v3/submit_task_v2"
GET_RESULT = f"{BASE_URL}/api/common/v3/get_result_v2"
CANCEL = f"{BASE_URL}/api/common/v3/cancel_task"
REQ_KEY = "realman_avatar_picture_omni20_cv"
API_KEY = "test-cv-key"  # pragma: allowlist secret


@pytest.fixture
def service() -> OmniHumanService:
    return OmniHumanService(
        gateway=VisionCvGateway(
            api_key=API_KEY, base_url=BASE_URL, timeout=10.0, connect_timeout=5.0
        )
    )


def submit_request(**overrides: object) -> OmniHumanSubmitRequest:
    fields: dict[str, object] = {
        "image_url": "https://media.example.com/face.png",
        "audio_url": "https://media.example.com/voice.mp3",
    }
    fields.update(overrides)
    return OmniHumanSubmitRequest.model_validate(fields)


def envelope(code: int = 10000, message: str = "Success", data: object = None) -> dict[str, object]:
    return {
        "code": code,
        "data": data,
        "message": message,
        "request_id": "req-1",
        "status": code,
        "time_elapsed": "10ms",
    }


class TestSubmit:
    @respx.mock
    async def test_sends_exact_path_headers_and_body(
        self, service: OmniHumanService, capsys: pytest.CaptureFixture[str]
    ) -> None:
        route = respx.post(SUBMIT).mock(
            return_value=httpx.Response(200, json=envelope(data={"task_id": "t-1"}))
        )

        submission = await service.create(
            submit_request(prompt="She waves", enable_watermark=False)
        )

        sent = route.calls.last.request
        assert sent.headers["Authorization"] == f"Bearer {API_KEY}"
        assert sent.headers["Content-Type"] == "application/json"
        assert json.loads(sent.content) == {
            "req_key": REQ_KEY,
            "prompt": "She waves",
            "audio_url": "https://media.example.com/voice.mp3",
            "image_urls": ["https://media.example.com/face.png"],
            "enable_watermark": False,
        }
        assert submission.task_id == "t-1"
        assert submission.request_id == "req-1"
        assert API_KEY not in capsys.readouterr().err

    @respx.mock
    async def test_omits_prompt_when_absent(self, service: OmniHumanService) -> None:
        route = respx.post(SUBMIT).mock(
            return_value=httpx.Response(200, json=envelope(data={"task_id": "t-1"}))
        )

        await service.create(submit_request())

        body = json.loads(route.calls.last.request.content)
        assert "prompt" not in body
        assert body["enable_watermark"] is True

    @respx.mock
    async def test_business_error_with_http_200_raises(self, service: OmniHumanService) -> None:
        respx.post(SUBMIT).mock(
            return_value=httpx.Response(
                200, json=envelope(code=50215, message="Input invalid for this service.")
            )
        )

        with pytest.raises(ProviderError) as exc_info:
            await service.create(submit_request())

        error = exc_info.value.error
        assert error.provider == "byteplus-vision-cv"
        assert error.code == "50215"
        assert error.request_id == "req-1"
        assert error.retryable is False
        assert "Input invalid" in error.message

    @respx.mock
    @pytest.mark.parametrize("code", [50429, 50430])
    async def test_rate_limit_codes_are_retryable(
        self, service: OmniHumanService, code: int
    ) -> None:
        respx.post(SUBMIT).mock(
            return_value=httpx.Response(429, json=envelope(code=code, message="limit"))
        )

        with pytest.raises(ProviderError) as exc_info:
            await service.create(submit_request())

        assert exc_info.value.error.retryable is True
        assert exc_info.value.error.ambiguous_completion is not True

    @respx.mock
    async def test_content_filter_is_not_retryable(self, service: OmniHumanService) -> None:
        respx.post(SUBMIT).mock(
            return_value=httpx.Response(
                500, json=envelope(code=50510, message="Algovproxy Risk Error")
            )
        )

        with pytest.raises(ProviderError) as exc_info:
            await service.create(submit_request())

        error = exc_info.value.error
        assert error.code == "50510"
        assert error.retryable is False
        assert error.ambiguous_completion is False

    @respx.mock
    @pytest.mark.parametrize("code", [50500, 50501])
    async def test_internal_error_on_submit_is_ambiguous_and_not_retryable(
        self, service: OmniHumanService, code: int
    ) -> None:
        respx.post(SUBMIT).mock(
            return_value=httpx.Response(500, json=envelope(code=code, message="Internal Error"))
        )

        with pytest.raises(ProviderError) as exc_info:
            await service.create(submit_request())

        assert exc_info.value.error.retryable is False
        assert exc_info.value.error.ambiguous_completion is True

    @respx.mock
    async def test_timeout_is_ambiguous(self, service: OmniHumanService) -> None:
        respx.post(SUBMIT).mock(side_effect=httpx.ReadTimeout("slow"))

        with pytest.raises(ProviderError) as exc_info:
            await service.create(submit_request())

        error = exc_info.value.error
        assert error.code == "TIMEOUT"
        assert error.ambiguous_completion is True
        assert error.retryable is False

    @respx.mock
    async def test_missing_task_id_raises_invalid_response(self, service: OmniHumanService) -> None:
        respx.post(SUBMIT).mock(return_value=httpx.Response(200, json=envelope(data={})))

        with pytest.raises(ProviderError) as exc_info:
            await service.create(submit_request())

        assert exc_info.value.error.code == "INVALID_RESPONSE"
        assert exc_info.value.error.ambiguous_completion is True

    @respx.mock
    async def test_non_json_success_raises_invalid_response(
        self, service: OmniHumanService
    ) -> None:
        respx.post(SUBMIT).mock(return_value=httpx.Response(200, text="<html>"))

        with pytest.raises(ProviderError) as exc_info:
            await service.create(submit_request())

        assert exc_info.value.error.code == "INVALID_RESPONSE"

    @respx.mock
    async def test_error_message_redacts_urls(self, service: OmniHumanService) -> None:
        respx.post(SUBMIT).mock(
            return_value=httpx.Response(
                200,
                json=envelope(code=50215, message="bad https://media.example.com/face.png?sig=x"),
            )
        )

        with pytest.raises(ProviderError) as exc_info:
            await service.create(submit_request())

        assert "media.example.com" not in exc_info.value.error.message
        assert "[REDACTED_URL]" in exc_info.value.error.message

    @respx.mock
    async def test_error_message_drops_echoed_response_body_and_is_bounded(
        self, service: OmniHumanService
    ) -> None:
        echoed = (
            "Download Url Error.: download image url failed: wrong status code: 404, "
            "body: <!doctype html><html><body>" + "x" * 5000 + "</body></html>"
        )
        respx.post(SUBMIT).mock(
            return_value=httpx.Response(400, json=envelope(code=50220, message=echoed))
        )

        with pytest.raises(ProviderError) as exc_info:
            await service.create(submit_request())

        message = exc_info.value.error.message
        assert "<html>" not in message
        assert "xxxx" not in message
        assert message.startswith("Download Url Error.: download image url failed")
        assert len(message) <= 600

    @respx.mock
    async def test_long_plain_message_is_truncated(self, service: OmniHumanService) -> None:
        respx.post(SUBMIT).mock(
            return_value=httpx.Response(200, json=envelope(code=50215, message="y" * 1000))
        )

        with pytest.raises(ProviderError) as exc_info:
            await service.create(submit_request())

        assert len(exc_info.value.error.message) <= 600


NESTED_INVALID_AUDIO = (
    'Internal RPC Error: : [{"gateway_code":10001,"gateway_msg":"RPC Internal Error",'
    '"req_key":"avatar_far2_create_video","algo_code":10001,"algo_msg":"Ark API POST '
    '/contents/generations/tasks failed with HTTP 400: {\\"error\\":{\\"code\\":'
    '\\"InvalidParameter\\",\\"message\\":\\"The parameter `content[2]` specified in the '
    "request is not valid: the parameter audio duration (seconds) specified in the request must "
    "be greater than or equal to 4 for model dreamina-seedance-2-0-mini in r2v. Request id: "
    '0217921124561908\\",\\"param\\":\\"content[2]\\",\\"type\\":\\"BadRequest\\"}}"}]'
)
NESTED_INTERNAL_SERVER_ERROR = (
    'Internal RPC Error: : [{"algo_msg":"Ark API POST /contents/generations/tasks failed with '
    'HTTP 503: {\\"error\\":{\\"code\\":\\"ServiceUnavailable\\",'
    '\\"message\\":\\"try later\\"}}"}]'
)


class TestNestedProviderFailures:
    """The provider reports processing failures as HTTP 500 / 50501 wrapping an inner error."""

    @respx.mock
    async def test_get_reports_inner_rejection_as_a_failed_task_with_the_real_reason(
        self, service: OmniHumanService
    ) -> None:
        respx.post(GET_RESULT).mock(
            return_value=httpx.Response(
                500, json=envelope(code=50501, message=NESTED_INVALID_AUDIO)
            )
        )

        result = await service.get("t-1")

        assert result.status == "failed"
        assert result.error_code == "InvalidParameter"
        assert result.error_message is not None
        assert "audio duration" in result.error_message
        assert "greater than or equal to 4" in result.error_message
        assert "gateway_code" not in result.error_message
        assert len(result.error_message) <= 600

    @respx.mock
    async def test_get_failed_result_also_applies_to_http_200_envelopes(
        self, service: OmniHumanService
    ) -> None:
        respx.post(GET_RESULT).mock(
            return_value=httpx.Response(
                200,
                json=envelope(code=50501, message=NESTED_INVALID_AUDIO, data={"status": "done"}),
            )
        )

        result = await service.get("t-1")

        assert result.status == "failed"
        assert "greater than or equal to 4" in (result.error_message or "")

    @respx.mock
    async def test_get_still_raises_retryable_for_a_genuine_internal_error(
        self, service: OmniHumanService
    ) -> None:
        respx.post(GET_RESULT).mock(
            return_value=httpx.Response(
                500, json=envelope(code=50501, message="Internal RPC Error")
            )
        )

        with pytest.raises(ProviderError) as exc_info:
            await service.get("t-1")

        assert exc_info.value.error.retryable is True

    @respx.mock
    async def test_get_still_raises_retryable_when_the_inner_error_is_a_server_error(
        self, service: OmniHumanService
    ) -> None:
        respx.post(GET_RESULT).mock(
            return_value=httpx.Response(
                500, json=envelope(code=50501, message=NESTED_INTERNAL_SERVER_ERROR)
            )
        )

        with pytest.raises(ProviderError) as exc_info:
            await service.get("t-1")

        assert exc_info.value.error.retryable is True

    @respx.mock
    async def test_submit_inner_rejection_is_not_retryable_nor_ambiguous_and_keeps_the_reason(
        self, service: OmniHumanService
    ) -> None:
        respx.post(SUBMIT).mock(
            return_value=httpx.Response(
                500, json=envelope(code=50501, message=NESTED_INVALID_AUDIO)
            )
        )

        with pytest.raises(ProviderError) as exc_info:
            await service.create(submit_request())

        error = exc_info.value.error
        assert error.retryable is False
        assert error.ambiguous_completion is False
        assert "greater than or equal to 4" in error.message
        assert error.message.startswith("InvalidParameter")

    @respx.mock
    async def test_long_unrecognised_messages_keep_their_diagnostic_tail_within_the_bound(
        self, service: OmniHumanService
    ) -> None:
        message = "Algorithm failure: " + "step ok; " * 40 + "ROOT CAUSE: bitrate unsupported"
        respx.post(SUBMIT).mock(
            return_value=httpx.Response(200, json=envelope(code=50215, message=message))
        )

        with pytest.raises(ProviderError) as exc_info:
            await service.create(submit_request())

        assert "ROOT CAUSE: bitrate unsupported" in exc_info.value.error.message


class TestSubmitRequestValidation:
    def test_rejects_non_https_media(self) -> None:
        with pytest.raises(ValidationError):
            submit_request(image_url="http://media.example.com/face.png")
        with pytest.raises(ValidationError):
            submit_request(audio_url="file:///tmp/voice.mp3")

    def test_blank_prompt_becomes_none(self) -> None:
        assert submit_request(prompt="   ").prompt is None

    def test_overlong_prompt_rejected(self) -> None:
        with pytest.raises(ValidationError):
            submit_request(prompt="x" * 2001)


class TestGet:
    @respx.mock
    @pytest.mark.parametrize(
        ("provider_status", "expected"),
        [
            ("in_queue", "queued"),
            ("generating", "running"),
            ("not_found", "not_found"),
            ("expired", "expired"),
            ("canceled", "canceled"),
        ],
    )
    async def test_non_terminal_and_terminal_status_mapping(
        self, service: OmniHumanService, provider_status: str, expected: str
    ) -> None:
        route = respx.post(GET_RESULT).mock(
            return_value=httpx.Response(200, json=envelope(data={"status": provider_status}))
        )

        result = await service.get("t-1")

        assert json.loads(route.calls.last.request.content) == {
            "req_key": REQ_KEY,
            "task_id": "t-1",
        }
        assert result.status == expected
        assert result.video_url is None

    @respx.mock
    async def test_done_returns_video_url_and_expiry(self, service: OmniHumanService) -> None:
        respx.post(GET_RESULT).mock(
            return_value=httpx.Response(
                200,
                json=envelope(
                    data={"status": "done", "video_url": "https://out.example.com/v.mp4"}
                ),
            )
        )

        result = await service.get("t-1")

        assert result.status == "succeeded"
        assert str(result.video_url) == "https://out.example.com/v.mp4"
        assert result.video_url_expires_at is not None

    @respx.mock
    async def test_done_without_video_url_is_invalid_response(
        self, service: OmniHumanService
    ) -> None:
        respx.post(GET_RESULT).mock(
            return_value=httpx.Response(200, json=envelope(data={"status": "done"}))
        )

        with pytest.raises(ProviderError) as exc_info:
            await service.get("t-1")

        assert exc_info.value.error.code == "INVALID_RESPONSE"

    @respx.mock
    async def test_done_with_business_failure_is_failed_result(
        self, service: OmniHumanService
    ) -> None:
        respx.post(GET_RESULT).mock(
            return_value=httpx.Response(
                200,
                json=envelope(code=50510, message="Risk", data={"status": "done"}),
            )
        )

        result = await service.get("t-1")

        assert result.status == "failed"
        assert result.error_code == "50510"
        assert result.error_message == "Risk"

    @respx.mock
    async def test_unknown_status_is_invalid_response(self, service: OmniHumanService) -> None:
        respx.post(GET_RESULT).mock(
            return_value=httpx.Response(200, json=envelope(data={"status": "mystery"}))
        )

        with pytest.raises(ProviderError) as exc_info:
            await service.get("t-1")

        assert exc_info.value.error.code == "INVALID_RESPONSE"

    @respx.mock
    async def test_access_denied_raises(self, service: OmniHumanService) -> None:
        respx.post(GET_RESULT).mock(
            return_value=httpx.Response(
                403, json=envelope(code=50400, message="Access Denied: Access Denied")
            )
        )

        with pytest.raises(ProviderError) as exc_info:
            await service.get("t-1")

        assert exc_info.value.error.code == "50400"
        assert exc_info.value.error.http_status == 403

    @respx.mock
    async def test_internal_error_on_get_is_retryable(self, service: OmniHumanService) -> None:
        respx.post(GET_RESULT).mock(
            return_value=httpx.Response(500, json=envelope(code=50500, message="Internal Error"))
        )

        with pytest.raises(ProviderError) as exc_info:
            await service.get("t-1")

        assert exc_info.value.error.retryable is True
        assert exc_info.value.error.ambiguous_completion is not True

    @respx.mock
    async def test_timeout_is_retryable_not_ambiguous(self, service: OmniHumanService) -> None:
        respx.post(GET_RESULT).mock(side_effect=httpx.ReadTimeout("slow"))

        with pytest.raises(ProviderError) as exc_info:
            await service.get("t-1")

        assert exc_info.value.error.retryable is True
        assert exc_info.value.error.ambiguous_completion is False


class TestCancel:
    @respx.mock
    async def test_cancel_success(self, service: OmniHumanService) -> None:
        route = respx.post(CANCEL).mock(
            return_value=httpx.Response(
                200, json=envelope(data={"req_key": REQ_KEY, "task_id": "t-1"})
            )
        )

        result = await service.cancel("t-1")

        assert json.loads(route.calls.last.request.content) == {
            "req_key": REQ_KEY,
            "task_id": "t-1",
        }
        assert result.canceled is True
        assert result.message is None

    @respx.mock
    async def test_already_started_is_typed_result_not_exception(
        self, service: OmniHumanService
    ) -> None:
        respx.post(CANCEL).mock(
            return_value=httpx.Response(
                200,
                json=envelope(code=50217, message="Cancel not allow: task already been executed"),
            )
        )

        result = await service.cancel("t-1")

        assert result.canceled is False
        assert result.message is not None
        assert "already" in result.message

    @respx.mock
    async def test_other_failures_raise(self, service: OmniHumanService) -> None:
        respx.post(CANCEL).mock(
            return_value=httpx.Response(200, json=envelope(code=50400, message="Access Denied"))
        )

        with pytest.raises(ProviderError) as exc_info:
            await service.cancel("t-1")

        assert exc_info.value.error.code == "50400"

    @respx.mock
    async def test_timeout_is_ambiguous(self, service: OmniHumanService) -> None:
        respx.post(CANCEL).mock(side_effect=httpx.ReadTimeout("slow"))

        with pytest.raises(ProviderError) as exc_info:
            await service.cancel("t-1")

        assert exc_info.value.error.ambiguous_completion is True
