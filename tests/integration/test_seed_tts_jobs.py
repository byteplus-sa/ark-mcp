"""TTS through the actual MCP job protocol with a synthetic provider stream."""

from __future__ import annotations

import asyncio
import base64
import json

import httpx
import pytest
import respx
from fastmcp import Client
from fastmcp.exceptions import ToolError
from fastmcp_tasks import call_tool_task
from mcp.shared.exceptions import MCPError

from ark_mcp.config.env import get_settings
from ark_mcp.server import create_server

URL = "https://voice.test.example.com/api/v3/text-to-speech/test-voice/streaming"
ARGUMENTS = {"input": {"text": "Hello", "voice_id": "test-voice"}}
PRONUNCIATION_RULES = ["北京/(bei3)(jing1)", "omg/oh my god"]


def stream_response() -> httpx.Response:
    events = [
        {"event": "ready", "session_id": "session"},
        {"event": "audio", "audio": base64.b64encode(b"synthetic mp3").decode()},
        {"event": "final", "is_final": True, "reason": "TTS_STREAM_FINAL_REASON_CLOSED"},
    ]
    return httpx.Response(200, text="\n".join(json.dumps(event) for event in events))


@pytest.mark.parametrize("native", [False, True])
@respx.mock
async def test_job_returns_audio_readable_as_mcp_resource(test_env: None, native: bool) -> None:
    route = respx.post(URL).mock(return_value=stream_response())
    arguments = {
        "input": ARGUMENTS["input"] | {"pronunciation_dictionary": {"tone": PRONUNCIATION_RULES}}
    }
    async with Client(create_server(get_settings())) as client:
        if native:
            task = await call_tool_task(client, "seed_tts_generate", arguments)
            result = await task.result()
            output = result.structured_content
        else:
            accepted = await client.call_tool(
                "ark_job_submit",
                {
                    "input": {
                        "tool_name": "seed_tts_generate",
                        "arguments": arguments,
                    }
                },
            )
            job_id = accepted.structured_content["job_id"]
            async with asyncio.timeout(5):
                while True:
                    snapshot = await client.call_tool("ark_job_get", {"input": {"job_id": job_id}})
                    state = snapshot.structured_content
                    if state["status"] != "working":
                        assert state["status"] == "completed"
                        assert not state["result"]["is_error"]
                        output = state["result"]["structured_content"]
                        break
                    await asyncio.sleep(0.01)
        assert output["model"] == "dola-seed-tts-3.0"
        assert output["artifact"]["mime_type"] == "audio/mpeg"
        resource = await client.read_resource(output["artifact"]["uri"])
        assert base64.b64decode(resource[0].blob) == b"synthetic mp3"
        assert route.call_count == 1
        body = json.loads(route.calls.last.request.content)
        assert body["pronunciation_dictionary"] == {"tone": PRONUNCIATION_RULES}


@respx.mock
async def test_foreground_rejected_before_provider_dispatch(test_env: None) -> None:
    route = respx.post(URL).mock(return_value=stream_response())
    async with Client(create_server(get_settings()), mode="legacy") as client:
        with pytest.raises((ToolError, MCPError)):
            await client.call_tool("seed_tts_generate", ARGUMENTS)
    assert not route.called


@respx.mock
async def test_invalid_job_arguments_rejected_before_provider_dispatch(test_env: None) -> None:
    route = respx.post(URL).mock(return_value=stream_response())
    async with Client(create_server(get_settings())) as client:
        result = await client.call_tool(
            "ark_job_submit",
            {
                "input": {
                    "tool_name": "seed_tts_generate",
                    "arguments": {
                        "input": {
                            "text": "Hello",
                            "ssml": "<speak>Hello</speak>",
                            "voice_id": "test-voice",
                        }
                    },
                }
            },
            raise_on_error=False,
        )
        assert result.is_error
    assert not route.called
