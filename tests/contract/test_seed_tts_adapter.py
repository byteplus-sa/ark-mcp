"""Seed TTS 3 HTTP JSONL contract, using synthetic audio only."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from ark_mcp.domain.errors import ProviderError
from ark_mcp.providers.seed_speech.client import SeedSpeechGateway
from ark_mcp.providers.seed_speech.seed_tts import SeedTtsService

BASE = "https://voice.test.example.com"
PATH = "/api/v3/text-to-speech/test-voice/streaming"
READY = {"event": "ready", "session_id": "session-test"}
FINAL = {"event": "final", "is_final": True, "reason": "TTS_STREAM_FINAL_REASON_CLOSED"}


def audio(data: bytes) -> dict[str, str]:
    return {"event": "audio", "audio": base64.b64encode(data).decode()}


def wire(*events: object) -> bytes:
    return b"\n".join(json.dumps(event).encode() for event in events)


class FragmentedStream(httpx.AsyncByteStream):
    def __init__(self, body: bytes, failure: Exception | None = None) -> None:
        self.body = body
        self.failure = failure
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for offset in range(0, len(self.body), 7):
            yield self.body[offset : offset + 7]
        if self.failure:
            raise self.failure

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
async def service() -> AsyncIterator[SeedTtsService]:
    service = SeedTtsService(SeedSpeechGateway(api_key="synthetic", base_url=BASE))
    yield service
    await service.close()


@respx.mock
async def test_split_lines_decode_each_chunk_and_map_word_seconds(
    service: SeedTtsService,
) -> None:
    stream = FragmentedStream(
        wire(
            READY,
            audio(b"a"),
            {
                "event": "alignment",
                "text": "Hello",
                "words": [{"word": "Hello", "startTime": 0.1, "endTime": 0.6}],
                "phonemes": [],
            },
            audio(b"bc"),
            FINAL,
        )
    )
    route = respx.post(BASE + PATH).mock(
        return_value=httpx.Response(200, stream=stream, headers={"X-Tt-Logid": "log-test"})
    )
    result = await service.synthesize(
        {"text": "Hello", "model_id": "dola-seed-tts-3.0"},
        voice_id="test-voice",
        request_id="req-test",
        max_audio_bytes=10,
    )
    assert result.audio == b"abc"
    assert result.session_id == "session-test"
    assert result.provider_log_id == "log-test"
    assert result.alignments[0].words[0].start_time == 0.1
    assert result.alignments[0].words[0].end_time == 0.6
    assert stream.closed
    request = route.calls.last.request
    assert request.headers["X-Api-Key"] == "synthetic"
    assert request.headers["X-Request-Id"] == "req-test"
    assert "X-Api-Request-Id" not in request.headers
    assert "Authorization" not in request.headers
    assert json.loads(request.content)["model_id"] == "dola-seed-tts-3.0"


@pytest.mark.parametrize(
    "body",
    [
        b"",
        wire(READY, audio(b"x")),
        wire(READY, FINAL),
        wire(audio(b"x"), FINAL),
        wire(READY, audio(b"x"), {**FINAL, "reason": "other"}),
        wire(READY, audio(b"x"), {**FINAL, "is_final": False}),
        wire(READY, {"event": "audio", "audio": "invalid!"}, FINAL),
        wire(READY, {"event": "audio", "audio": 42}, FINAL),
        wire(READY, [], FINAL),
        wire(READY) + b"\nnot-json",
        wire(READY, {"event": "unexpected"}, FINAL),
        wire(
            READY,
            {
                "event": "alignment",
                "text": "x",
                "words": [{"word": "x", "startTime": 1, "endTime": 0}],
            },
            audio(b"x"),
            FINAL,
        ),
    ],
)
@respx.mock
async def test_invalid_or_incomplete_stream_is_ambiguous_and_never_retried(
    service: SeedTtsService, body: bytes
) -> None:
    route = respx.post(BASE + PATH).mock(return_value=httpx.Response(200, content=body))
    with pytest.raises(ProviderError) as error:
        await service.synthesize(
            {"text": "Hello"}, voice_id="test-voice", request_id="req-test", max_audio_bytes=10
        )
    assert not error.value.retryable
    assert error.value.ambiguous_completion
    assert route.call_count == 1


@respx.mock
async def test_audio_limit_is_checked_before_persistence(service: SeedTtsService) -> None:
    respx.post(BASE + PATH).mock(
        return_value=httpx.Response(200, content=wire(READY, audio(b"12345"), FINAL))
    )
    with pytest.raises(ProviderError, match="limit") as error:
        await service.synthesize(
            {"text": "Hello"}, voice_id="test-voice", request_id="req-test", max_audio_bytes=4
        )
    assert error.value.code == "OUTPUT_TOO_LARGE"
    assert error.value.ambiguous_completion


@pytest.mark.parametrize(
    "status,ambiguous,retryable",
    [(400, False, False), (401, False, False), (429, False, True), (503, True, False)],
)
@respx.mock
async def test_nested_http_errors_keep_provider_identifiers(
    service: SeedTtsService, status: int, ambiguous: bool, retryable: bool
) -> None:
    respx.post(BASE + PATH).mock(
        return_value=httpx.Response(
            status,
            json={
                "event": "error",
                "error": {
                    "type": "validation_error",
                    "code": "voice_access_denied",
                    "message": "Voice unavailable",
                    "request_id": "provider-request",
                    "logid": "provider-log",
                },
            },
        )
    )
    with pytest.raises(ProviderError) as error:
        await service.synthesize(
            {"text": "Hello"}, voice_id="test-voice", request_id="req-test", max_audio_bytes=10
        )
    assert error.value.code == "voice_access_denied"
    assert error.value.request_id == "provider-request"
    assert "provider-log" in error.value.message
    assert error.value.ambiguous_completion == ambiguous
    assert error.value.retryable == retryable


@pytest.mark.parametrize("status,ambiguous", [(400, False), (429, False), (503, True)])
@respx.mock
async def test_unreadable_error_body_retains_http_status(
    service: SeedTtsService, status: int, ambiguous: bool
) -> None:
    stream = FragmentedStream(b"", httpx.ReadError("error body disconnected"))
    route = respx.post(BASE + PATH).mock(
        return_value=httpx.Response(status, stream=stream, headers={"X-Tt-Logid": "log-test"})
    )
    with pytest.raises(ProviderError) as error:
        await service.synthesize(
            {"text": "Hello"}, voice_id="test-voice", request_id="req-test", max_audio_bytes=10
        )
    assert error.value.code == "TTS_PROVIDER_ERROR"
    assert error.value.http_status == status
    assert error.value.request_id == "req-test"
    assert error.value.ambiguous_completion is ambiguous
    assert error.value.retryable is (status == 429)
    assert "log-test" in error.value.message
    assert stream.closed
    assert route.call_count == 1


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ReadTimeout("timeout"),
        httpx.ReadError("broken"),
        httpx.DecodingError("bad compression"),
    ],
)
@respx.mock
async def test_stream_disconnect_closes_connection_without_retry(
    service: SeedTtsService, failure: Exception
) -> None:
    stream = FragmentedStream(wire(READY, audio(b"x")) + b"\n", failure)
    route = respx.post(BASE + PATH).mock(return_value=httpx.Response(200, stream=stream))
    with pytest.raises(ProviderError) as error:
        await service.synthesize(
            {"text": "Hello"}, voice_id="test-voice", request_id="req-test", max_audio_bytes=10
        )
    assert error.value.ambiguous_completion
    assert not error.value.retryable
    assert route.call_count == 1
    assert stream.closed


@respx.mock
async def test_closed_final_returns_without_waiting_for_http_eof(service: SeedTtsService) -> None:
    stream = FragmentedStream(
        wire(READY, audio(b"x"), FINAL) + b"\n", httpx.ReadError("EOF never arrived")
    )
    respx.post(BASE + PATH).mock(return_value=httpx.Response(200, stream=stream))
    result = await service.synthesize(
        {"text": "Hello"}, voice_id="test-voice", request_id="req-test", max_audio_bytes=10
    )
    assert result.audio == b"x"
    assert stream.closed


@respx.mock
async def test_voice_id_is_encoded_as_one_path_segment(service: SeedTtsService) -> None:
    route = respx.post(BASE + "/api/v3/text-to-speech/a%2Fb%3Fc/streaming").mock(
        return_value=httpx.Response(200, content=wire(READY, audio(b"x"), FINAL))
    )
    await service.synthesize(
        {"text": "Hello"}, voice_id="a/b?c", request_id="req-test", max_audio_bytes=10
    )
    assert route.called


@pytest.mark.parametrize("terminated", [False, True])
@respx.mock
async def test_oversized_event_rejected_with_or_without_newline(
    service: SeedTtsService, monkeypatch: pytest.MonkeyPatch, terminated: bool
) -> None:
    monkeypatch.setattr("ark_mcp.providers.seed_speech.seed_tts.MAX_EVENT_BYTES", 64)
    stream = FragmentedStream(b"x" * 65 + (b"\n" if terminated else b""))
    respx.post(BASE + PATH).mock(return_value=httpx.Response(200, stream=stream))
    with pytest.raises(ProviderError) as error:
        await service.synthesize(
            {"text": "Hello"}, voice_id="test-voice", request_id="req-test", max_audio_bytes=10
        )
    assert error.value.code == "OUTPUT_TOO_LARGE"
    assert stream.closed


@respx.mock
async def test_error_after_audio_is_ambiguous(service: SeedTtsService) -> None:
    respx.post(BASE + PATH).mock(
        return_value=httpx.Response(
            200,
            content=wire(
                READY, audio(b"x"), {"event": "error", "error": {"code": "internal_error"}}
            ),
        )
    )
    with pytest.raises(ProviderError) as error:
        await service.synthesize(
            {"text": "Hello"}, voice_id="test-voice", request_id="req-test", max_audio_bytes=10
        )
    assert error.value.code == "internal_error"
    assert error.value.ambiguous_completion
    assert not error.value.retryable


class WaitingStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield wire(READY) + b"\n"
        self.started.set()
        await asyncio.Event().wait()

    async def aclose(self) -> None:
        self.closed = True


@respx.mock
async def test_cancellation_closes_stream(service: SeedTtsService) -> None:
    stream = WaitingStream()
    route = respx.post(BASE + PATH).mock(return_value=httpx.Response(200, stream=stream))
    task = asyncio.create_task(
        service.synthesize(
            {"text": "Hello"},
            voice_id="test-voice",
            request_id="req-test",
            max_audio_bytes=10,
        )
    )
    await asyncio.wait_for(stream.started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.closed
    assert route.call_count == 1


@respx.mock
async def test_wall_clock_deadline_closes_stalled_stream() -> None:
    stream = WaitingStream()
    respx.post(BASE + PATH).mock(return_value=httpx.Response(200, stream=stream))
    service = SeedTtsService(
        SeedSpeechGateway(api_key="synthetic", base_url=BASE),  # pragma: allowlist secret
        timeout_seconds=0.01,
    )
    try:
        with pytest.raises(ProviderError) as error:
            await service.synthesize(
                {"text": "Hello"},
                voice_id="test-voice",
                request_id="req-test",
                max_audio_bytes=10,
            )
        assert error.value.code == "TIMEOUT"
        assert error.value.ambiguous_completion
        assert stream.closed
    finally:
        await service.close()
