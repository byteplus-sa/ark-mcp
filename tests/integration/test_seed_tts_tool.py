"""Seed TTS validation, dispatch, billing and artifact integration."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from fastmcp.tools import ToolResult
from pydantic import ValidationError

from ark_mcp.artifacts.store import ArtifactPersistenceError
from ark_mcp.runtime import BudgetExceededError
from ark_mcp.tools.seed_tts_generate import (
    SeedTtsGenerateInput,
    SeedTtsGenerateOutput,
    seed_tts_generate,
)
from tests.fixtures.fake_context import FakeContext

URL = "https://voice.test.example.com/api/v3/text-to-speech/test-voice/streaming"
VOICE = {"voice_id": "test-voice"}


def response() -> httpx.Response:
    return httpx.Response(
        200,
        text="\n".join(
            json.dumps(item)
            for item in [
                {"event": "ready", "session_id": "provider-session"},
                {"event": "audio", "audio": base64.b64encode(b"synthetic audio").decode()},
                {"event": "final", "is_final": True, "reason": "TTS_STREAM_FINAL_REASON_CLOSED"},
            ]
        ),
    )


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"text": ""},
        {"text": "  "},
        {"text": "x" * 10001},
        {"ssml": "x" * 10001},
        {"ssml": " "},
        {"text": "x", "ssml": "<speak>x</speak>"},
        {"text": "x", "model": "seed-tts-2.0"},
        {"text": "x", "voice_id": ".."},
        {"text": "x", "voice_id": " "},
        {"text": "x", "output_format": {"format": "wav"}},
        {"text": "x", "output_format": {"format": "ogg_opus", "sample_rate": 24000}},
        {"text": "x", "output_format": {"format": "pcm", "bit_rate": 64000}},
        {"text": "x", "output_format": {"sample_rate": 8000, "bit_rate": 160000}},
        {"text": "x", "voice_settings": {"speed": 2.01}},
        {"text": "x", "voice_settings": {"volume": 0.49}},
        {"text": "x", "voice_settings": {"pitch": 13}},
        {"text": "x", "silence_duration": 30001},
        {"text": "x", "silence_duration": float("nan")},
        {"text": "x", "pronunciation_dictionary": [{}]},
        {"text": "x", "pronunciation_dictionary": {"unexpected": ["word/replacement"]}},
        {"text": "x", "pronunciation_dictionary": {"tone": "word/replacement"}},
        {"text": "x", "pronunciation_dictionary": {"tone": [None]}},
        {"text": "x", "pronunciation_dictionary": {"tone": ["missing separator"]}},
        {"text": "x", "pronunciation_dictionary": {"tone": [" /replacement"]}},
        {"text": "x", "pronunciation_dictionary": {"tone": ["word/ "]}},
        {"text": "x", "pronunciation_dictionary": {"tone": ["word/" + "x" * 996]}},
        {"text": "x", "pronunciation_dictionary": {"tone": ["word/replacement"] * 101}},
        {"text": "x", "persist": False},
    ],
)
def test_invalid_input_rejected_before_dispatch(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SeedTtsGenerateInput.model_validate(VOICE | fields)


def test_input_character_boundaries_and_format_defaults() -> None:
    value = SeedTtsGenerateInput(**VOICE, text="x" * 10000)
    assert len(value.text) == 10000
    assert value.output_format.resolved().sample_rate == 24000
    assert value.output_format.resolved().bit_rate == 64000
    opus = SeedTtsGenerateInput(**VOICE, text="x", output_format={"format": "ogg_opus"})
    assert opus.output_format.resolved().sample_rate == 48000


def test_pronunciation_dictionary_accepts_local_boundaries() -> None:
    max_length_rule = "word/" + "x" * 995
    rules = [max_length_rule] * 100
    for tone in ([], rules):
        value = SeedTtsGenerateInput.model_validate(
            VOICE | {"text": "Hello", "pronunciation_dictionary": {"tone": tone}}
        )
        assert value.pronunciation_dictionary.tone == tone


@pytest.mark.parametrize(
    "encoding,mime,extension",
    [
        ("mp3", "audio/mpeg", ".mp3"),
        ("pcm", "audio/pcm", ".pcm"),
        ("ogg_opus", "audio/ogg", ".ogg"),
    ],
)
@respx.mock
async def test_full_stream_persisted_with_matching_mime_and_local_extension(
    fake_ctx: FakeContext,
    temp_store: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    encoding: str,
    mime: str,
    extension: str,
) -> None:
    monkeypatch.setenv("OUTPUT_ROOTS", str(tmp_path))
    from ark_mcp.config.env import get_settings

    get_settings.cache_clear()
    route = respx.post(URL).mock(return_value=response())
    result = await seed_tts_generate(
        SeedTtsGenerateInput(
            **VOICE,
            text="Hello",
            output_format={"format": encoding},
            output_path=f"{tmp_path}/",
        ),
        fake_ctx,
    )
    assert isinstance(result, SeedTtsGenerateOutput)
    assert result.model == "dola-seed-tts-3.0"
    assert result.artifact.mime_type == mime
    assert result.artifact.uri.startswith("seed-media://artifacts/")
    assert result.artifact.source_expires_at is None
    assert result.artifact.local_path.endswith(extension)
    stored = await temp_store.get(result.artifact.id)
    assert stored.data == b"synthetic audio"
    body = json.loads(route.calls.last.request.content)
    assert body["model_id"] == "dola-seed-tts-3.0"
    assert "model" not in body
    assert "ssml" not in body
    assert "voice_id" not in body
    assert "output_path" not in body
    assert "pronunciation_dictionary" not in body
    assert body["output_format"]["format"] == encoding
    assert (body["output_format"].get("bit_rate") is None) == (encoding == "pcm")
    assert result.session_id == "provider-session"


@respx.mock
async def test_ssml_and_delivery_controls_reach_provider(
    fake_ctx: FakeContext,
) -> None:
    route = respx.post(URL).mock(return_value=response())
    result = await seed_tts_generate(
        SeedTtsGenerateInput(
            **VOICE,
            ssml="<speak>Hello</speak>",
            language_code="en-US",
            session_id="conversation-test",
            enable_subtitle=True,
            text_normalization=False,
            enable_markdown_filter=True,
            silence_duration=30000,
            voice_settings={
                "speed": 0.5,
                "volume": 2,
                "pitch": -12,
                "instructions": ["Warm"],
                "similarity_boost": True,
            },
        ),
        fake_ctx,
    )
    assert isinstance(result, SeedTtsGenerateOutput)
    body = json.loads(route.calls.last.request.content)
    assert "text" not in body
    assert body["ssml"] == "<speak>Hello</speak>"
    assert body["session_id"] == "conversation-test"
    assert body["voice_settings"]["similarity_boost"]
    assert body["voice_settings"]["speed"] == 0.5
    assert body["silence_duration"] == 30000
    assert body["enable_subtitle"]
    assert not body["text_normalization"]


@respx.mock
async def test_incomplete_stream_returns_error_without_persisting(
    fake_ctx: FakeContext,
    temp_store: Any,
) -> None:
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, text='{"event":"ready","session_id":"s"}')
    )
    result = await seed_tts_generate(SeedTtsGenerateInput(**VOICE, text="Hello"), fake_ctx)
    assert isinstance(result, ToolResult)
    assert result.is_error
    assert "INCOMPLETE_STREAM" in result.content[0].text
    assert route.call_count == 1


@pytest.mark.parametrize(
    "outcome,committed",
    [
        ("success", True),
        ("truncated", True),
        ("rejected", False),
        ("connect_timeout", False),
        ("pool_timeout", False),
        ("read_timeout", True),
        ("storage_failure", True),
    ],
)
@respx.mock
async def test_billing_preserved_for_success_or_ambiguous_completion(
    fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch, outcome: str, committed: bool
) -> None:
    runtime = fake_ctx.lifespan_context["runtime"]
    ledger = runtime.budget_ledger
    commit = AsyncMock(wraps=ledger.commit)
    release = AsyncMock(wraps=ledger.release)
    reserve = AsyncMock(wraps=ledger.reserve)
    monkeypatch.setattr(ledger, "commit", commit)
    monkeypatch.setattr(ledger, "release", release)
    monkeypatch.setattr(ledger, "reserve", reserve)
    if outcome == "truncated":
        provider_response = httpx.Response(200, text='{"event":"ready","session_id":"s"}')
    elif outcome == "rejected":
        provider_response = httpx.Response(
            400, json={"event": "error", "error": {"code": "invalid_text"}}
        )
    else:
        provider_response = response()
    failure = {
        "connect_timeout": httpx.ConnectTimeout("connect timed out"),
        "pool_timeout": httpx.PoolTimeout("pool acquisition timed out"),
        "read_timeout": httpx.ReadTimeout("read timed out"),
    }.get(outcome)
    route = respx.post(URL).mock(return_value=provider_response, side_effect=failure)
    if outcome == "storage_failure":
        monkeypatch.setattr(
            runtime.artifact_store,
            "put_base64",
            AsyncMock(
                side_effect=ArtifactPersistenceError(
                    "storage_failed",
                    "Storage unavailable.",
                    retryable=False,
                )
            ),
        )
    result = await seed_tts_generate(
        SeedTtsGenerateInput(**VOICE, ssml="<speak>x</speak>"), fake_ctx
    )
    assert commit.await_count == int(committed)
    assert release.await_count == int(not committed)
    assert reserve.call_args.args[1].amount_usd == pytest.approx(
        len("<speak>x</speak>") * 20 / 1_000_000
    )
    assert route.call_count == 1
    if failure is not None:
        assert isinstance(result, ToolResult) and result.is_error
        expected_code = "TIMEOUT" if outcome == "read_timeout" else "CONNECTION_ERROR"
        assert expected_code in result.content[0].text
    if outcome == "storage_failure":
        assert result.artifact.id == "inline-fallback"
        assert base64.b64decode(result.artifact.fallback_data) == b"synthetic audio"


@respx.mock
async def test_budget_rejection_prevents_synthesis(
    fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    route = respx.post(URL).mock(return_value=response())
    monkeypatch.setattr(
        fake_ctx.lifespan_context["runtime"].budget_ledger,
        "reserve",
        AsyncMock(side_effect=BudgetExceededError("Daily budget exceeded.")),
    )
    with pytest.raises(BudgetExceededError):
        await seed_tts_generate(SeedTtsGenerateInput(**VOICE, text="Hello"), fake_ctx)
    assert not route.called
