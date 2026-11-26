"""Seed Audio 1.5 requests, responses, and artifacts through the MCP tool."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import struct
import wave
from typing import Any

import httpx
import pytest
import respx
from fastmcp import Client
from fastmcp.tools import ToolResult
from pydantic import ValidationError

from ark_mcp.config.env import get_settings
from ark_mcp.security.auth_context import PrincipalContext
from ark_mcp.server import create_server
from ark_mcp.tools import _seed_audio_1_5
from ark_mcp.tools.seed_audio_generate import SeedAudioGenerateInput, seed_audio_generate
from ark_mcp.tools.seed_media_persist_url import SeedMediaPersistUrlInput, seed_media_persist_url
from tests.fixtures.fake_context import FakeContext

MODEL = "dola-seed-audio-1-5-261128"
ARK_ENDPOINT = "https://ark.test.example.com/api/v3/contents/generations"
AUDIO_URL = "https://output.example.volces.com/scene.wav"
VIDEO_URL = "https://media.example.com/reference.mp4"
VOICE_URL = "https://media.example.com/voice.wav"
PROMPT = "Say 'The garden is open' in a warm voice."


def wav_bytes(*, seconds: int = 2, sample_rate: int = 8000, channels: int = 1) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setparams((channels, 2, sample_rate, 0, "NONE", "not compressed"))
        writer.writeframes(b"\0\0" * (seconds * sample_rate * channels))
    return buffer.getvalue()


def response_content(**content: Any) -> dict[str, Any]:
    return {
        "id": "test-generation",
        "model": MODEL,
        "content": {"audios": [{"url": AUDIO_URL}], **content},
        "output_format": "wav",
        "usage": {"duration_ms": 3750, "final_duration_ms": 2500},
    }


@respx.mock
async def test_separation_accepts_inline_audio_longer_than_a_reference_clip(
    fake_ctx: FakeContext,
) -> None:
    request = respx.post(ARK_ENDPOINT).respond(200, json=response_content())
    source = "data:audio/wav;base64," + base64.b64encode(wav_bytes(seconds=36)).decode()

    await seed_audio_generate(
        SeedAudioGenerateInput(
            model=MODEL, text_prompt=PROMPT, separate_audio=source, persist=False
        ),
        fake_ctx,
    )

    assert json.loads(request.calls.last.request.content)["content"][1] == {
        "type": "audio_url",
        "audio_url": {"url": source},
        "role": "separate_audio",
    }


@pytest.mark.parametrize("seconds", [1, 361])
def test_inline_separation_rejects_wav_outside_provider_duration(seconds: int) -> None:
    source = (
        "data:audio/wav;base64,"
        + base64.b64encode(wav_bytes(seconds=seconds, sample_rate=1000)).decode()
    )
    with pytest.raises(ValidationError, match="duration"):
        SeedAudioGenerateInput(model=MODEL, text_prompt=PROMPT, separate_audio=source)


def test_reference_audio_uses_its_model_specific_size_limit() -> None:
    data = base64.b64encode(wav_bytes(seconds=24, sample_rate=96000, channels=3)).decode()
    arguments = {"text_prompt": PROMPT, "audio_references": [{"kind": "base64", "data": data}]}

    parsed = SeedAudioGenerateInput.model_validate({"model": MODEL, **arguments})
    assert len(parsed.audio_references[0].data or "") == len(data)
    with pytest.raises(ValidationError, match="decoded size"):
        SeedAudioGenerateInput.model_validate(arguments)


def test_modelark_reference_wav_requires_two_seconds() -> None:
    data = base64.b64encode(wav_bytes(seconds=1)).decode()
    arguments = {"text_prompt": PROMPT, "audio_references": [{"kind": "base64", "data": data}]}

    with pytest.raises(ValidationError, match="duration"):
        SeedAudioGenerateInput.model_validate({"model": MODEL, **arguments})
    assert SeedAudioGenerateInput.model_validate(arguments).audio_references


@respx.mock
async def test_modelark_accepts_a_base64_bmp_reference(fake_ctx: FakeContext) -> None:
    request = respx.post(ARK_ENDPOINT).respond(200, json=response_content())
    pixels = bytes(320 * 320 * 3)
    header = struct.pack("<2sI4xIIiiHH24x", b"BM", 54 + len(pixels), 54, 40, 320, 320, 1, 24)
    data = base64.b64encode(header + pixels).decode()
    payload = SeedAudioGenerateInput.model_validate(
        {
            "model": MODEL,
            "text_prompt": PROMPT,
            "image_reference": {"kind": "base64", "mime_type": "image/bmp", "data": data},
            "persist": False,
        }
    )
    await seed_audio_generate(payload, fake_ctx)

    assert json.loads(request.calls.last.request.content)["content"][1] == {
        "type": "image_url",
        "image_url": {"url": f"data:image/bmp;base64,{data}"},
        "role": "reference_image",
    }


@respx.mock
async def test_generated_long_audio_is_persisted(fake_ctx: FakeContext) -> None:
    respx.post(ARK_ENDPOINT).respond(200, json=response_content())
    audio = wav_bytes(seconds=60, channels=2, sample_rate=48000)
    respx.get("https://93.184.216.34/scene.wav").respond(
        200, content=audio, headers={"Content-Type": "audio/wav"}
    )

    result = await seed_audio_generate(
        SeedAudioGenerateInput(model=MODEL, text_prompt=PROMPT), fake_ctx
    )

    assert not isinstance(result, ToolResult)
    assert result.audio_tracks and result.audio_tracks[0].uri.startswith("seed-media://artifacts/")
    assert result.audio_tracks[0].bytes == len(audio)


@respx.mock
async def test_large_provider_url_track_can_be_recovered_without_changing_default_limit(
    fake_ctx: FakeContext,
) -> None:
    respx.post(ARK_ENDPOINT).respond(200, json=response_content())
    audio = wav_bytes(seconds=60, channels=2, sample_rate=48000)
    respx.get("https://93.184.216.34/scene.wav").respond(
        200, content=audio, headers={"Content-Type": "audio/wav"}
    )
    result = await seed_audio_generate(
        SeedAudioGenerateInput(model=MODEL, text_prompt=PROMPT, persist=False), fake_ctx
    )
    assert not isinstance(result, ToolResult)
    assert result.audio_tracks
    track = result.audio_tracks[0]
    args = {"url": track.uri, "media_type": track.media_type, "mime_type": track.mime_type}

    with pytest.raises(ValueError, match="output_too_large"):
        await seed_media_persist_url(SeedMediaPersistUrlInput.model_validate(args), fake_ctx)

    recovered = await seed_media_persist_url(
        SeedMediaPersistUrlInput.model_validate({**args, "max_download_bytes": 90 * 1024 * 1024}),
        fake_ctx,
    )
    assert recovered.artifact.uri.startswith("seed-media://artifacts/")
    assert recovered.artifact.bytes == len(audio)


@pytest.mark.parametrize("source", ["reference_video", "dubbing_video"])
@respx.mock
async def test_shared_http_tos_video_is_rejected_before_provider_dispatch(
    fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    request = respx.post(ARK_ENDPOINT).respond(200, json=response_content())
    remote = PrincipalContext(principal_id="shared-team", tenant_id="shared-team", transport="http")
    monkeypatch.setattr(_seed_audio_1_5, "get_principal", lambda _ctx: remote)
    mode = (
        {"text_prompt": PROMPT}
        if source == "reference_video"
        else {"dubbing_config": {"target_language": "fr"}}
    )
    payload = SeedAudioGenerateInput.model_validate(
        {
            "model": MODEL,
            source: "tos://private-bucket/demos/video.mp4",
            **mode,
        }
    )

    with pytest.raises(ValueError, match=r"TOS.*trusted local"):
        await seed_audio_generate(payload, fake_ctx)

    assert request.call_count == 0

    https_payload = SeedAudioGenerateInput.model_validate(
        {"model": MODEL, source: VIDEO_URL, **mode, "persist": False}
    )
    await seed_audio_generate(https_payload, fake_ctx)
    assert request.call_count == 1


@pytest.mark.parametrize(
    ("media_type", "max_bytes"),
    [("video", 1024), ("audio", 90 * 1024 * 1024 + 1)],
)
def test_recovery_cap_is_bounded_and_audio_only(media_type: str, max_bytes: int) -> None:
    with pytest.raises(ValidationError, match="max_download_bytes"):
        SeedMediaPersistUrlInput.model_validate(
            {
                "url": AUDIO_URL,
                "media_type": media_type,
                "mime_type": "audio/wav",
                "max_download_bytes": max_bytes,
            }
        )


@respx.mock
async def test_local_tos_video_is_forwarded(fake_ctx: FakeContext) -> None:
    request = respx.post(ARK_ENDPOINT).respond(200, json=response_content())
    source = "tos://authorized-bucket/demos/video.mp4"
    payload = SeedAudioGenerateInput(
        model=MODEL,
        text_prompt=PROMPT,
        reference_video=source,
        persist=False,
    )

    await seed_audio_generate(payload, fake_ctx)

    assert json.loads(request.calls.last.request.content)["content"][1]["video_url"] == {
        "url": source
    }


@respx.mock
async def test_text_generation_sends_modelark_controls_and_returns_provider_duration(
    fake_ctx: FakeContext,
) -> None:
    request = respx.post(ARK_ENDPOINT).respond(
        200, json=response_content(), headers={"X-Request-Id": "test-provider-request"}
    )

    result = await seed_audio_generate(
        SeedAudioGenerateInput.model_validate(
            {
                "model": MODEL,
                "text_prompt": PROMPT,
                "persist": False,
                "output": {
                    "format": "wav",
                    "sample_rate": 44100,
                    "speech_rate": 0,
                    "loudness_rate": -25,
                    "pitch_rate": 4,
                },
            }
        ),
        fake_ctx,
    )

    assert request.call_count == 1
    assert request.calls.last.request.headers["Authorization"] == (
        f"Bearer {get_settings().modelark_api_key}"
    )
    assert json.loads(request.calls.last.request.content) == {
        "model": MODEL,
        "content": [{"type": "text", "text": PROMPT}],
        "output_format": "wav",
        "audio_config": {
            "sample_rate": 44100,
            "speech_rate": 0,
            "loudness_rate": -25,
            "pitch_rate": 4,
        },
    }
    assert not isinstance(result, ToolResult)
    assert result.model == MODEL
    assert result.generation_id == "test-generation"
    assert result.duration_seconds == 2.5
    assert result.billing_duration_seconds == 3.75
    assert len(result.audio_tracks) == 1
    assert result.audio_tracks[0].uri == AUDIO_URL
    assert result.audio_tracks[0].mime_type == "audio/wav"


@respx.mock
async def test_reference_video_and_audio_keep_roles_and_audio_order(fake_ctx: FakeContext) -> None:
    request = respx.post(ARK_ENDPOINT).respond(200, json=response_content())
    inline_audio = base64.b64encode(wav_bytes()).decode()
    include = ["video_url", "subtitles.srt", "subtitles.jsonl"]
    references = [
        {"kind": "url", "url": VOICE_URL},
        {"kind": "base64", "data": inline_audio, "mime_type": "audio/wav"},
        *[{"kind": "url", "url": f"https://media.example.com/voice-{i}.mp3"} for i in range(4)],
    ]

    result = await seed_audio_generate(
        SeedAudioGenerateInput.model_validate(
            {
                "model": MODEL,
                "text_prompt": PROMPT,
                "reference_video": VIDEO_URL,
                "audio_references": references,
                "include": include,
                "persist": False,
            }
        ),
        fake_ctx,
    )

    assert not isinstance(result, ToolResult)
    assert request.call_count == 1
    body = json.loads(request.calls.last.request.content)
    assert body.get("output_format", "wav") == "wav"
    assert body["include"] == include
    assert body["content"][0] == {"type": "text", "text": PROMPT}
    assert [item for item in body["content"] if item["type"] == "video_url"] == [
        {"type": "video_url", "video_url": {"url": VIDEO_URL}, "role": "reference_video"}
    ]
    assert [item for item in body["content"] if item["type"] == "audio_url"] == [
        {"type": "audio_url", "audio_url": {"url": url}, "role": "reference_audio"}
        for url in [VOICE_URL, f"data:audio/wav;base64,{inline_audio}"]
        + [reference["url"] for reference in references[2:]]
    ]


@respx.mock
async def test_separation_returns_every_track_in_provider_order(fake_ctx: FakeContext) -> None:
    audios = [
        {
            "url": f"https://output.example.volces.com/{name}.wav",
            "type": kind,
            "description": name,
        }
        for kind, name in [("speaker", "voices"), ("sfx", "effects"), ("bgm", "music")]
    ]
    request = respx.post(ARK_ENDPOINT).respond(200, json=response_content(audios=audios))
    prompt = "Separate voices, music, and sound effects into distinct tracks."

    result = await seed_audio_generate(
        SeedAudioGenerateInput.model_validate(
            {"model": MODEL, "text_prompt": prompt, "separate_audio": VOICE_URL, "persist": False}
        ),
        fake_ctx,
    )

    assert request.call_count == 1
    assert json.loads(request.calls.last.request.content)["content"] == [
        {"type": "text", "text": prompt},
        {"type": "audio_url", "audio_url": {"url": VOICE_URL}, "role": "separate_audio"},
    ]
    assert not isinstance(result, ToolResult)
    assert [(track.track_type, track.description, track.uri) for track in result.audio_tracks] == [
        (track["type"], track["description"], track["url"]) for track in audios
    ]


@respx.mock
async def test_dubbing_sends_only_video_and_returns_requested_video(fake_ctx: FakeContext) -> None:
    video_output = "https://output.example.volces.com/dubbed.mp4"
    subtitles = [
        {"format": "srt", "url": "https://output.example.volces.com/fr.srt"},
        {"format": "jsonl", "url": "https://output.example.volces.com/fr.jsonl"},
    ]
    source_subtitles = [
        {"format": "srt", "url": "https://output.example.volces.com/en.srt"},
        {"format": "jsonl", "url": "https://output.example.volces.com/en.jsonl"},
    ]
    request = respx.post(ARK_ENDPOINT).respond(
        200,
        json=response_content(
            video_url=video_output, subtitles=subtitles, subtitles_source=source_subtitles
        ),
    )
    include = [
        "video_url",
        "subtitles.srt",
        "subtitles.jsonl",
        "subtitles_source.srt",
        "subtitles_source.jsonl",
    ]
    dubbing_config = {
        "source_language": "en",
        "target_language": "fr",
        "custom_subtitle": {"format": "srt", "url": "https://media.example.com/target.srt"},
        "dialogue_enhancement": "medium",
    }

    result = await seed_audio_generate(
        SeedAudioGenerateInput.model_validate(
            {
                "model": MODEL,
                "dubbing_video": VIDEO_URL,
                "dubbing_config": dubbing_config,
                "include": include,
                "persist": False,
            }
        ),
        fake_ctx,
    )

    assert request.call_count == 1
    body = json.loads(request.calls.last.request.content)
    assert body["content"] == [
        {"type": "video_url", "video_url": {"url": VIDEO_URL}, "role": "dubbing_video"}
    ]
    assert body["dubbing_config"] == dubbing_config
    assert body["include"] == include
    assert not isinstance(result, ToolResult)
    assert result.video_artifact is not None
    assert result.video_artifact.uri == video_output
    assert result.video_artifact.mime_type == "video/mp4"
    assert [{"format": item.format, "url": item.url} for item in result.subtitles] == subtitles
    assert [
        {"format": item.format, "url": item.url} for item in result.subtitles_source
    ] == source_subtitles


@respx.mock
async def test_mcp_job_uses_server_configuration_and_persists_audio(
    test_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    base_url = "https://runtime.ark.test.example.com/api/v3"
    settings = get_settings().model_copy(update={"modelark_base_url": base_url})
    monkeypatch.setenv("BYTEPLUS_MODELARK_API_KEY", "")
    get_settings.cache_clear()
    generation = respx.post(f"{base_url}/contents/generations").respond(
        200, json=response_content()
    )
    audio = wav_bytes()
    download = respx.get("https://93.184.216.34/scene.wav").respond(
        200, content=audio, headers={"Content-Type": "audio/wav"}
    )
    async with Client(create_server(settings)) as client:
        accepted = await client.call_tool(
            "ark_job_submit",
            {
                "input": {
                    "tool_name": "seed_audio_generate",
                    "arguments": {"input": {"model": MODEL, "text_prompt": PROMPT}},
                }
            },
        )
        job_id = accepted.structured_content["job_id"]
        async with asyncio.timeout(5):
            while True:
                snapshot = await client.call_tool("ark_job_get", {"input": {"job_id": job_id}})
                state = snapshot.structured_content
                if state["status"] == "working":
                    await asyncio.sleep(0.01)
                    continue
                assert state["status"] == "completed"
                assert not state["result"]["is_error"]
                output = state["result"]["structured_content"]
                break

        assert output["model"] == MODEL
        track = output["audio_tracks"][0]
        assert track["uri"].startswith("seed-media://artifacts/")
        resource = await client.read_resource(track["uri"])
        assert base64.b64decode(resource[0].blob) == audio

    assert generation.call_count == 1
    assert (
        generation.calls.last.request.headers["Authorization"]
        == f"Bearer {settings.modelark_api_key}"
    )
    assert download.call_count == 1
    assert download.calls.last.request.headers["Host"] == "output.example.volces.com"


@respx.mock
async def test_parallel_jobs_share_the_two_request_preview_limit(fake_ctx: FakeContext) -> None:
    active = 0
    peak_active = 0
    first_pair_ready = asyncio.Event()

    async def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal active, peak_active
        active += 1
        peak_active = max(peak_active, active)
        if active == 2:
            first_pair_ready.set()
        try:
            await first_pair_ready.wait()
            await asyncio.sleep(0.02)
            return httpx.Response(200, json=response_content())
        finally:
            active -= 1

    request = respx.post(ARK_ENDPOINT).mock(side_effect=respond)
    request_input = SeedAudioGenerateInput.model_validate(
        {"model": MODEL, "text_prompt": PROMPT, "persist": False}
    )

    async with asyncio.timeout(5):
        results = await asyncio.gather(
            *(seed_audio_generate(request_input, fake_ctx) for _ in range(3))
        )

    assert request.call_count == 3
    assert peak_active == 2
    assert all(not isinstance(result, ToolResult) for result in results)


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        pytest.param({}, "text_prompt", id="missing-prompt"),
        pytest.param({"text_prompt": ""}, "text_prompt", id="empty-prompt"),
        pytest.param({"dubbing_video": VIDEO_URL}, "dubbing_config", id="missing-dubbing-config"),
        pytest.param(
            {
                "dubbing_video": VIDEO_URL,
                "dubbing_config": {"target_language": "fr"},
                "text_prompt": PROMPT,
            },
            "dubbing_video|text_prompt",
            id="text-with-dubbing",
        ),
        pytest.param({"separate_audio": VOICE_URL}, "text_prompt", id="missing-separation-prompt"),
        pytest.param(
            {"text_prompt": PROMPT, "reference_video": VIDEO_URL, "separate_audio": VOICE_URL},
            "reference_video|separate_audio",
            id="mixed-video-and-separation",
        ),
        pytest.param(
            {"text_prompt": PROMPT, "reference_video": "https://127.0.0.1/private.mp4"},
            "Invalid media URL",
            id="private-network-video",
        ),
        pytest.param(
            {"text_prompt": PROMPT, "audio_references": [{"kind": "url", "url": VOICE_URL}] * 7},
            "audio_references|6",
            id="too-many-audio-references",
        ),
        pytest.param(
            {"text_prompt": PROMPT, "audio_references": [{"kind": "base64", "data": "invalid!"}]},
            "Base64",
            id="invalid-base64",
        ),
        pytest.param({"text_prompt": PROMPT, "output": {"format": "ogg"}}, "ogg", id="legacy-ogg"),
        pytest.param(
            {"text_prompt": PROMPT, "output": {"format": "ogg_opus", "sample_rate": 44100}},
            "48000",
            id="opus-with-invalid-rate",
        ),
        pytest.param(
            {
                "dubbing_video": VIDEO_URL,
                "dubbing_config": {
                    "target_language": "fr",
                    "custom_subtitle": {
                        "format": "srt",
                        "url": "https://media.example.com/target.srt",
                    },
                    "glossaries": [{"source": "garden", "target": "jardin"}],
                },
            },
            "custom_subtitle|glossaries",
            id="custom-subtitles-with-glossaries",
        ),
        pytest.param({"text_prompt": PROMPT, "variations": 2}, "variations", id="batch-count"),
        pytest.param({"variation_prompts": [PROMPT]}, "variation_prompts", id="batch-prompts"),
        pytest.param(
            {"text_prompt": PROMPT, "watermark": {"enable": True}},
            "(?i)watermark",
            id="legacy-watermark",
        ),
        pytest.param(
            {"text_prompt": PROMPT, "output": {"subtitle": True}}, "subtitle", id="legacy-subtitles"
        ),
    ],
)
def test_invalid_modelark_requests_fail_validation(arguments: dict[str, Any], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        SeedAudioGenerateInput.model_validate({"model": MODEL, **arguments})


@pytest.mark.parametrize("failure", ["read-timeout", "bad-gateway", "malformed-error"])
@respx.mock
async def test_ambiguous_provider_failure_does_not_resubmit(
    fake_ctx: FakeContext, failure: str
) -> None:
    request = respx.post(ARK_ENDPOINT)
    if failure == "read-timeout":
        request.mock(side_effect=httpx.ReadTimeout("test timeout"))
    elif failure == "malformed-error":
        request.respond(502, json={"error": {"message": None}}, headers={"Retry-After": "-1"})
    else:
        request.respond(502, json={"error": {"code": "BadGateway", "message": "Gateway error"}})

    result = await seed_audio_generate(
        SeedAudioGenerateInput.model_validate({"model": MODEL, "text_prompt": PROMPT}), fake_ctx
    )

    assert request.call_count == 1
    assert isinstance(result, ToolResult)
    assert result.is_error
    assert "ambiguous_completion=True" in result.content[0].text


async def test_encoded_request_limit_is_enforced_before_dispatch(
    fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_seed_audio_1_5, "_MAX_REQUEST_BYTES", 100)
    payload = SeedAudioGenerateInput(model=MODEL, text_prompt="雨" * 30)

    with pytest.raises(ValueError, match="request body exceeds 64 MiB"):
        await seed_audio_generate(payload, fake_ctx)


@respx.mock
async def test_provider_success_without_audio_is_a_tool_error(fake_ctx: FakeContext) -> None:
    request = respx.post(ARK_ENDPOINT).respond(200, json=response_content(audios=[]))

    result = await seed_audio_generate(
        SeedAudioGenerateInput.model_validate({"model": MODEL, "text_prompt": PROMPT}), fake_ctx
    )

    assert request.call_count == 1
    assert isinstance(result, ToolResult)
    assert result.is_error
