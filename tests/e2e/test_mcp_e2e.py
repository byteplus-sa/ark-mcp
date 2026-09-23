"""End-to-end tests exercising the full MCP protocol layer.

Uses the FastMCP in-memory client transport to connect directly to the
server instance, mock provider HTTP responses with respx, and verify
tool discovery, tool invocation, artifact persistence, and resource
retrieval through the actual MCP protocol — not direct function calls.
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
import respx
from fastmcp import Client
from fastmcp.exceptions import ToolError
from fastmcp_tasks import call_tool_task
from mcp.shared.exceptions import MCPError

from ark_mcp.config.env import get_settings
from ark_mcp.config.model_capabilities import refresh_capability_registry
from ark_mcp.domain.errors import NormalizedProviderError, ProviderError
from ark_mcp.providers.modelark.schemas import (
    ChatCompletionProviderResponse,
    SeedanceTaskListResponse,
    SeedanceTaskResponse,
)
from ark_mcp.providers.modelark.seedance import SeedanceService
from ark_mcp.providers.modelark.understanding import SeedUnderstandingService
from ark_mcp.providers.seed_speech.schemas import SeedAudioProviderResponse
from ark_mcp.providers.seed_speech.seed_audio import SeedAudioService
from ark_mcp.security.safe_downloader import DownloadedMedia

if TYPE_CHECKING:
    from ark_mcp.server import FastMCP

ARK_BASE = "https://ark.test.example.com/api/v3"


@pytest.fixture
def e2e_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> object:
    monkeypatch.setenv("BYTEPLUS_MODELARK_API_KEY", "sk-test-modelark")
    monkeypatch.setenv("BYTEPLUS_SEED_SPEECH_API_KEY", "sk-test-speech")
    monkeypatch.setenv("BYTEPLUS_VOD_MEDIAKIT_API_KEY", "test-mediakit-key")
    monkeypatch.setenv("BYTEPLUS_MODELARK_3D_ENABLED", "false")
    monkeypatch.setenv("TOS_ACCESS_KEY", "ak-test-tos")
    monkeypatch.setenv("TOS_SECRET_KEY", "sk-test-tos")
    monkeypatch.setenv("TOS_BUCKET", "test-bucket")
    monkeypatch.setenv("BYTEPLUS_MODELARK_BASE_URL", ARK_BASE)
    monkeypatch.setenv("BYTEPLUS_SEED_AUDIO_BASE_URL", "https://voice.test.example.com")
    monkeypatch.setenv("SEEDREAM_DEFAULT_MODEL", "dola-seedream-5-0-pro-260628")
    monkeypatch.setenv("SEEDANCE_DEFAULT_MODEL", "dreamina-seedance-2-0-260128")
    monkeypatch.setenv("ARTIFACT_DIR", str(tmp_path / ".artifacts"))
    monkeypatch.setenv("ARTIFACT_BACKEND", "filesystem")

    get_settings.cache_clear()
    refresh_capability_registry()

    from ark_mcp.server import create_server

    yield SimpleNamespace(mcp=create_server(get_settings()))

    get_settings.cache_clear()
    refresh_capability_registry()


def _mock_seedream_response(
    img_b64: str | None = None,
    url: str | None = None,
    output_format: str | None = None,
    status: int = 200,
) -> httpx.Response:
    data: list[dict[str, object]] = []
    if img_b64 is not None:
        item: dict[str, object] = {"b64_json": img_b64}
        if output_format is not None:
            item["output_format"] = output_format
        data.append(item)
    elif url is not None:
        data.append({"url": url})
    body: dict[str, object] = {
        "created": 1721400000,
        "data": data,
        "usage": {"prompt_tokens": 10, "total_tokens": 20},
    }
    return httpx.Response(
        status,
        json=body,
        headers={"X-Request-Id": "req-e2e-123"},
    )


async def _call_background_tool(
    client: Client,
    name: str,
    arguments: dict[str, Any],
    *,
    raise_on_error: bool = True,
) -> Any:
    task = await call_tool_task(
        client,
        name,
        arguments,
        raise_on_error=raise_on_error,
    )
    return await task.result()


async def _call_compatibility_tool(
    client: Client,
    name: str,
    arguments: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    submitted = await client.call_tool(
        "ark_job_submit",
        {"input": {"tool_name": name, "arguments": arguments}},
    )
    job_id = submitted.structured_content["job_id"]
    async with asyncio.timeout(5):
        while True:
            snapshot = await client.call_tool(
                "ark_job_get",
                {"input": {"job_id": job_id}},
            )
            state = snapshot.structured_content
            if state["status"] != "working":
                assert state["status"] == "completed"
                assert state["result"] is not None
                return job_id, state["result"]
            await asyncio.sleep(0.01)


def _foreground_client(mcp: FastMCP) -> Client:
    return Client(mcp, mode="legacy")


class TestToolDiscovery:
    """Verify tools are discoverable through the MCP protocol."""

    async def test_lists_all_configured_tools(self, e2e_server: object) -> None:
        mcp: FastMCP = e2e_server.mcp  # type: ignore[attr-defined]
        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = {t.name for t in tools}
            assert tool_names == {
                "ark_job_cancel",
                "ark_job_capabilities",
                "ark_job_get",
                "ark_job_submit",
                "seed_audio_generate",
                "seed_audio_generate_variations",
                "seed_media_export_artifact",
                "seed_media_get_artifact",
                "seed_media_persist_url",
                "seedream_edit_image",
                "seedream_generate_image",
                "seedream_generate_image_variations",
                "seedance_create_task",
                "seedance_create_task_variations",
                "seedance_2_5_create_task",
                "seedance_2_5_create_task_variations",
                "seedance_get_task",
                "seedance_get_tasks",
                "seedance_list_tasks",
                "seedance_cancel_or_delete_task",
                "seed_understand",
                "seed_audio_understand",
                "speech_to_text",
                "media_upload",
                "media_upload_batch",
                "media_presign",
                "media_presign_batch",
                "vod_enhance_video",
                "vod_get_enhancement_task",
                "vod_transcode_video",
                "vod_get_transcode_task",
                "vod_separate_audio",
                "vod_get_audio_separation",
                "vod_add_subtitles",
                "vod_get_subtitle_addition_task",
                "vod_remove_subtitles",
                "vod_get_subtitle_removal_task",
            }

    async def test_tool_has_input_schema(self, e2e_server: object) -> None:
        mcp: FastMCP = e2e_server.mcp  # type: ignore[attr-defined]
        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "seedream_generate_image")
            schema = tool.input_schema
            assert schema is not None
            assert "properties" in schema
            assert "input" in schema["properties"]

    async def test_tool_has_output_schema(self, e2e_server: object) -> None:
        mcp: FastMCP = e2e_server.mcp  # type: ignore[attr-defined]
        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "seedream_generate_image")
            schema = tool.output_schema
            assert schema is not None
            assert "artifacts" in schema["properties"]
            assert "model" in schema["properties"]


class TestSeedUnderstandE2E:
    async def test_rejects_foreground_execution_before_provider_call(
        self,
        e2e_server: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        provider_call = AsyncMock()
        monkeypatch.setattr(SeedUnderstandingService, "generate", provider_call)

        mcp: FastMCP = e2e_server.mcp  # type: ignore[attr-defined]
        async with _foreground_client(mcp) as client:
            with pytest.raises(MCPError, match="requires the tasks extension"):
                await client.call_tool(
                    "seed_understand",
                    {"input": {"prompt": "Analyze the video"}},
                )

        provider_call.assert_not_awaited()

    async def test_runs_as_background_task_without_blocking_for_provider(
        self,
        e2e_server: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        provider_started = asyncio.Event()
        release_provider = asyncio.Event()

        async def delayed_generate(
            self: SeedUnderstandingService, request: object
        ) -> tuple[ChatCompletionProviderResponse, str | None]:
            provider_started.set()
            await release_provider.wait()
            return (
                ChatCompletionProviderResponse.model_validate(
                    {
                        "id": "chatcmpl-background",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": "done"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 1,
                            "completion_tokens": 1,
                            "total_tokens": 2,
                        },
                    }
                ),
                "req-background",
            )

        monkeypatch.setattr(SeedUnderstandingService, "generate", delayed_generate)

        mcp: FastMCP = e2e_server.mcp  # type: ignore[attr-defined]
        async with Client(mcp) as client:
            task = await call_tool_task(
                client,
                "seed_understand",
                {"input": {"prompt": "Analyze the video"}},
            )
            await asyncio.wait_for(provider_started.wait(), timeout=5)

            release_provider.set()
            result = await task.result()

        assert not result.is_error
        assert result.structured_content["choices"][0]["content"] == "done"


class TestBackgroundJobCompatibility:
    async def test_legacy_client_discovers_configured_background_targets(
        self,
        e2e_server: object,
    ) -> None:
        mcp: FastMCP = e2e_server.mcp  # type: ignore[attr-defined]
        async with _foreground_client(mcp) as client:
            result = await client.call_tool("ark_job_capabilities", {})

        targets = {target["tool_name"]: target for target in result.structured_content["targets"]}
        assert targets["seed_understand"]["task_mode"] == "required"
        assert targets["seedance_get_task"]["task_mode"] == "optional"
        assert "input" in targets["seed_understand"]["input_schema"]["properties"]
        assert "hyper3d_create_task" not in targets

    @pytest.mark.parametrize("tool_name", ["seedance_list_tasks", "ark_job_submit"])
    async def test_legacy_client_rejects_non_background_target(
        self,
        e2e_server: object,
        monkeypatch: pytest.MonkeyPatch,
        tool_name: str,
    ) -> None:
        provider_call = AsyncMock()
        submission_metric = Mock()
        monkeypatch.setattr(SeedUnderstandingService, "generate", provider_call)
        monkeypatch.setattr(
            "ark_mcp.tools.background_jobs.record_background_job_submission",
            submission_metric,
        )

        mcp: FastMCP = e2e_server.mcp  # type: ignore[attr-defined]
        async with _foreground_client(mcp) as client:
            with pytest.raises(ToolError, match="not supported"):
                await client.call_tool(
                    "ark_job_submit",
                    {
                        "input": {
                            "tool_name": tool_name,
                            "arguments": {"input": {}},
                        }
                    },
                )

        provider_call.assert_not_awaited()
        submission_metric.assert_not_called()

    async def test_legacy_client_rejects_unconfigured_background_target(
        self,
        e2e_server: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        submission_metric = Mock()
        monkeypatch.setattr(
            "ark_mcp.tools.background_jobs.record_background_job_submission",
            submission_metric,
        )

        mcp: FastMCP = e2e_server.mcp  # type: ignore[attr-defined]
        async with _foreground_client(mcp) as client:
            with pytest.raises(ToolError, match="not available"):
                await client.call_tool(
                    "ark_job_submit",
                    {
                        "input": {
                            "tool_name": "hyper3d_create_task",
                            "arguments": {"input": {"image_url": "https://example.com/a.png"}},
                        }
                    },
                )

        submission_metric.assert_called_once_with(
            target="hyper3d_create_task",
            status="rejected",
            path="compatibility",
        )

    @pytest.mark.parametrize("operation", ["ark_job_get", "ark_job_cancel"])
    async def test_legacy_client_cannot_access_unknown_job(
        self,
        e2e_server: object,
        operation: str,
    ) -> None:
        mcp: FastMCP = e2e_server.mcp  # type: ignore[attr-defined]
        async with _foreground_client(mcp) as client:
            with pytest.raises(ToolError, match="not available"):
                await client.call_tool(
                    operation,
                    {"input": {"job_id": "a" * 43}},
                )

    @pytest.mark.parametrize(
        ("arguments", "error_pattern"),
        [
            ({}, "Invalid arguments"),
            ({"input": {"prompt": ""}}, "validation error"),
        ],
    )
    async def test_legacy_client_rejects_invalid_arguments_before_job_creation(
        self,
        e2e_server: object,
        monkeypatch: pytest.MonkeyPatch,
        arguments: dict[str, object],
        error_pattern: str,
    ) -> None:
        provider_call = AsyncMock()
        submission_metric = Mock()
        monkeypatch.setattr(SeedUnderstandingService, "generate", provider_call)
        monkeypatch.setattr(
            "ark_mcp.tools.background_jobs.record_background_job_submission",
            submission_metric,
        )

        mcp: FastMCP = e2e_server.mcp  # type: ignore[attr-defined]
        async with _foreground_client(mcp) as client:
            with pytest.raises(ToolError, match=error_pattern):
                await client.call_tool(
                    "ark_job_submit",
                    {
                        "input": {
                            "tool_name": "seed_understand",
                            "arguments": arguments,
                        }
                    },
                )

        provider_call.assert_not_awaited()
        submission_metric.assert_called_once_with(
            target="seed_understand",
            status="rejected",
            path="compatibility",
        )

    async def test_legacy_client_runs_required_tool_without_task_extension(
        self,
        e2e_server: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        provider_started = asyncio.Event()
        release_provider = asyncio.Event()
        submission_metric = Mock()

        async def delayed_generate(
            self: SeedUnderstandingService, request: object
        ) -> tuple[ChatCompletionProviderResponse, str | None]:
            provider_started.set()
            await release_provider.wait()
            return (
                ChatCompletionProviderResponse.model_validate(
                    {
                        "id": "chatcmpl-compatibility",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": "compatible"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 1,
                            "completion_tokens": 1,
                            "total_tokens": 2,
                        },
                    }
                ),
                "req-compatibility",
            )

        monkeypatch.setattr(SeedUnderstandingService, "generate", delayed_generate)
        monkeypatch.setattr(
            "ark_mcp.tools.background_jobs.record_background_job_submission",
            submission_metric,
        )

        mcp: FastMCP = e2e_server.mcp  # type: ignore[attr-defined]
        async with _foreground_client(mcp) as client:
            submitted = await client.call_tool(
                "ark_job_submit",
                {
                    "input": {
                        "tool_name": "seed_understand",
                        "arguments": {"input": {"prompt": "Analyze the video"}},
                    }
                },
            )
            job_id = submitted.structured_content["job_id"]
            assert submitted.structured_content["status"] == "working"
            await asyncio.wait_for(provider_started.wait(), timeout=5)

            working = await client.call_tool(
                "ark_job_get",
                {"input": {"job_id": job_id}},
            )
            assert working.structured_content["status"] == "working"

            release_provider.set()
            async with asyncio.timeout(5):
                while True:
                    completed = await client.call_tool(
                        "ark_job_get",
                        {"input": {"job_id": job_id}},
                    )
                    if completed.structured_content["status"] != "working":
                        break
                    await asyncio.sleep(0.01)

        assert completed.structured_content["status"] == "completed"
        assert completed.structured_content["result"]["is_error"] is False
        assert (
            completed.structured_content["result"]["structured_content"]["choices"][0]["content"]
            == "compatible"
        )
        submission_metric.assert_called_once_with(
            target="seed_understand",
            status="accepted",
            path="compatibility",
        )

    async def test_legacy_client_cancels_owned_background_job(
        self,
        e2e_server: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        provider_started = asyncio.Event()
        provider_cancelled = asyncio.Event()

        async def delayed_generate(
            self: SeedUnderstandingService, request: object
        ) -> tuple[ChatCompletionProviderResponse, str | None]:
            provider_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                provider_cancelled.set()
            raise AssertionError("Cancelled provider unexpectedly resumed")

        monkeypatch.setattr(SeedUnderstandingService, "generate", delayed_generate)

        mcp: FastMCP = e2e_server.mcp  # type: ignore[attr-defined]
        async with _foreground_client(mcp) as client:
            submitted = await client.call_tool(
                "ark_job_submit",
                {
                    "input": {
                        "tool_name": "seed_understand",
                        "arguments": {"input": {"prompt": "Analyze the video"}},
                    }
                },
            )
            job_id = submitted.structured_content["job_id"]
            await asyncio.wait_for(provider_started.wait(), timeout=5)

            cancelled = await client.call_tool(
                "ark_job_cancel",
                {"input": {"job_id": job_id}},
            )
            assert cancelled.structured_content == {
                "job_id": job_id,
                "status": "cancelled",
            }
            await asyncio.wait_for(provider_cancelled.wait(), timeout=5)

            terminal = await client.call_tool(
                "ark_job_get",
                {"input": {"job_id": job_id}},
            )

        assert terminal.structured_content["status"] == "cancelled"

    async def test_legacy_client_preserves_terminal_tool_error(
        self,
        e2e_server: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def failed_generate(
            self: SeedUnderstandingService,
            request: object,
        ) -> tuple[ChatCompletionProviderResponse, str | None]:
            raise ProviderError(
                NormalizedProviderError(
                    provider="modelark",
                    operation="understanding",
                    http_status=400,
                    code="INVALID_ARGUMENT",
                    message="invalid media",
                    request_id="req-compatibility-error",
                    retryable=False,
                )
            )

        monkeypatch.setattr(SeedUnderstandingService, "generate", failed_generate)

        mcp: FastMCP = e2e_server.mcp  # type: ignore[attr-defined]
        async with _foreground_client(mcp) as client:
            _, result = await _call_compatibility_tool(
                client,
                "seed_understand",
                {"input": {"prompt": "Analyze invalid media"}},
            )

        assert result["is_error"] is True
        assert result["structured_content"] is None
        assert "INVALID_ARGUMENT" in result["content"][0]["text"]
        assert "req-compatibility-error" in result["content"][0]["text"]


class TestResourceTemplates:
    """Verify resource templates are registered and discoverable."""

    async def test_artifact_resource_template_registered(self, e2e_server: object) -> None:
        mcp: FastMCP = e2e_server.mcp  # type: ignore[attr-defined]
        async with Client(mcp) as client:
            templates = await client.list_resource_templates()
            artifact_tmpl = next(t for t in templates if "artifacts" in t.uri_template)
            assert "artifact_id" in artifact_tmpl.uri_template

    async def test_health_resource_registered(self, e2e_server: object) -> None:
        mcp: FastMCP = e2e_server.mcp  # type: ignore[attr-defined]
        async with Client(mcp) as client:
            resources = await client.list_resources()
            assert len(resources) >= 1


class TestSeedreamGenerateImageE2E:
    """Full-path E2E tests for seedream_generate_image via MCP protocol."""

    async def test_generate_image_with_b64_json_persisted(self, e2e_server: object) -> None:
        mcp: FastMCP = e2e_server.mcp  # type: ignore[attr-defined]
        img_b64 = base64.b64encode(b"fake-png-bytes").decode()

        with respx.mock:
            respx.post(f"{ARK_BASE}/images/generations").mock(
                return_value=_mock_seedream_response(img_b64=img_b64, output_format="png")
            )
            async with Client(mcp) as client:
                result = await _call_background_tool(
                    client,
                    "seedream_generate_image",
                    {"input": {"prompt": "a red circle"}},
                )

        assert not result.is_error
        data = result.structured_content
        assert data["model"] == "dola-seedream-5-0-pro-260628"
        assert len(data["artifacts"]) == 1
        artifact = data["artifacts"][0]
        assert artifact["uri"].startswith("seed-media://artifacts/")
        assert artifact["media_type"] == "image"
        assert artifact["mime_type"] == "image/png"
        assert artifact["id"] != "provider-url"
        assert data["usage"]["total_tokens"] == 20

    async def test_legacy_client_preserves_seedream_artifact_result(
        self,
        e2e_server: object,
    ) -> None:
        mcp: FastMCP = e2e_server.mcp  # type: ignore[attr-defined]
        img_b64 = base64.b64encode(b"compatibility-png-bytes").decode()

        with respx.mock:
            respx.post(f"{ARK_BASE}/images/generations").mock(
                return_value=_mock_seedream_response(img_b64=img_b64, output_format="png")
            )
            async with _foreground_client(mcp) as client:
                _, result = await _call_compatibility_tool(
                    client,
                    "seedream_generate_image",
                    {"input": {"prompt": "a blue square"}},
                )

        assert result["is_error"] is False
        artifact = result["structured_content"]["artifacts"][0]
        assert artifact["uri"].startswith("seed-media://artifacts/")
        assert artifact["mime_type"] == "image/png"

    async def test_generate_image_persist_false_returns_provider_url(
        self, e2e_server: object
    ) -> None:
        mcp: FastMCP = e2e_server.mcp  # type: ignore[attr-defined]
        provider_url = "https://tos-ap-southeast.bytepluses.com/out.png"

        with respx.mock:
            respx.post(f"{ARK_BASE}/images/generations").mock(
                return_value=_mock_seedream_response(url=provider_url)
            )
            async with Client(mcp) as client:
                result = await _call_background_tool(
                    client,
                    "seedream_generate_image",
                    {"input": {"prompt": "a blue square", "persist": False}},
                )

        assert not result.is_error
        data = result.structured_content
        assert len(data["artifacts"]) == 1
        artifact = data["artifacts"][0]
        assert artifact["id"] == "provider-url"
        assert artifact["uri"] == provider_url

    async def test_retrieve_artifact_after_generation(self, e2e_server: object) -> None:
        mcp: FastMCP = e2e_server.mcp  # type: ignore[attr-defined]
        raw_bytes = b"e2e-artifact-payload"
        img_b64 = base64.b64encode(raw_bytes).decode()

        with respx.mock:
            respx.post(f"{ARK_BASE}/images/generations").mock(
                return_value=_mock_seedream_response(img_b64=img_b64, output_format="png")
            )
            async with Client(mcp) as client:
                result = await _call_background_tool(
                    client,
                    "seedream_generate_image",
                    {"input": {"prompt": "a green triangle"}},
                )
                artifact_uri = result.structured_content["artifacts"][0]["uri"]
                content = await client.read_resource(artifact_uri)

        assert len(content) >= 1
        item = content[0]
        assert hasattr(item, "blob")
        assert base64.b64decode(item.blob) == raw_bytes
        assert item.mime_type == "image/png"

    async def test_provider_error_returns_error_result(self, e2e_server: object) -> None:
        mcp: FastMCP = e2e_server.mcp  # type: ignore[attr-defined]

        error_body = {
            "error": {
                "code": "FORBIDDEN",
                "message": "model not activated",
            }
        }

        with respx.mock:
            respx.post(f"{ARK_BASE}/images/generations").mock(
                return_value=httpx.Response(
                    403,
                    json=error_body,
                    headers={"X-Request-Id": "req-err-001"},
                )
            )
            async with Client(mcp) as client:
                result = await _call_background_tool(
                    client,
                    "seedream_generate_image",
                    {"input": {"prompt": "test"}},
                    raise_on_error=False,
                )

        assert result.is_error
        assert result.structured_content is None
        text = result.content[0].text
        assert "modelark generate_image failed" in text
        assert "code=FORBIDDEN" in text
        assert "request_id=req-err-001" in text

    async def test_batch_rejected_for_pro_model(self, e2e_server: object) -> None:
        mcp: FastMCP = e2e_server.mcp  # type: ignore[attr-defined]

        async with Client(mcp) as client:
            result = await _call_background_tool(
                client,
                "seedream_generate_image",
                {"input": {"prompt": "test", "max_images": 3}},
                raise_on_error=False,
            )

        assert result.is_error

    async def test_generate_image_with_url_persisted(self, e2e_server: object) -> None:
        mcp: FastMCP = e2e_server.mcp  # type: ignore[attr-defined]
        provider_url = "https://tos-ap-southeast.bytepluses.com/image.png"

        with (
            patch(
                "ark_mcp.security.safe_downloader.SafeDownloader.download",
                new=AsyncMock(
                    return_value=DownloadedMedia(
                        body=b"downloaded-png-data",
                        content_type="image/png",
                        final_url=provider_url,
                    )
                ),
            ),
            respx.mock,
        ):
            respx.post(f"{ARK_BASE}/images/generations").mock(
                return_value=_mock_seedream_response(url=provider_url)
            )
            async with Client(mcp) as client:
                result = await _call_background_tool(
                    client,
                    "seedream_generate_image",
                    {"input": {"prompt": "a purple star", "persist": True}},
                )
                artifact_uri = result.structured_content["artifacts"][0]["uri"]
                content = await client.read_resource(artifact_uri)

        assert not result.is_error
        data = result.structured_content
        assert len(data["artifacts"]) == 1
        assert data["artifacts"][0]["id"] != "provider-url"
        assert len(content) >= 1
        assert base64.b64decode(content[0].blob) == b"downloaded-png-data"


class TestHealthResourceE2E:
    """Verify the health resource is readable via MCP protocol."""

    async def test_read_health_resource(self, e2e_server: object) -> None:
        mcp: FastMCP = e2e_server.mcp  # type: ignore[attr-defined]
        async with Client(mcp) as client:
            content = await client.read_resource("seed-health://status")

        assert len(content) >= 1
        item = content[0]
        assert hasattr(item, "text")
        text = item.text
        assert "Ark Seed MCP Server" in text
        assert "healthy" in text
        assert "ModelArk configured: True" in text
        assert "Seed Audio configured: True" in text


class TestSeedAudioE2E:
    """Verify Seed Audio executes through the MCP protocol surface."""

    async def test_generates_and_persists_audio(
        self, e2e_server: object, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mcp: FastMCP = e2e_server.mcp  # type: ignore[attr-defined]
        audio_b64 = base64.b64encode(b"e2e-audio-data").decode()

        async def mock_generate(
            self: SeedAudioService,
            request: object,
            *,
            request_id: str | None = None,
        ) -> tuple[SeedAudioProviderResponse, str | None]:
            return (
                SeedAudioProviderResponse(
                    code=0,
                    message="success",
                    audio=audio_b64,
                    duration=2.5,
                    original_duration=3.0,
                ),
                "seed-speech-log-e2e",
            )

        async def mock_close(self: SeedAudioService) -> None:
            return None

        monkeypatch.setattr(SeedAudioService, "generate", mock_generate)
        monkeypatch.setattr(SeedAudioService, "close", mock_close)

        async with Client(mcp) as client:
            result = await _call_background_tool(
                client,
                "seed_audio_generate",
                {"input": {"text_prompt": "a short ambient scene"}},
            )
            artifact_uri = result.structured_content["artifact"]["uri"]
            content = await client.read_resource(artifact_uri)

        assert not result.is_error
        data = result.structured_content
        assert data["duration_seconds"] == 2.5
        assert data["billing_duration_seconds"] == 3.0
        assert data["provider_log_id"] == "seed-speech-log-e2e"
        assert data["artifact"]["media_type"] == "audio"
        assert base64.b64decode(content[0].blob) == b"e2e-audio-data"

    async def test_provider_error_returns_no_structured_content(
        self, e2e_server: object, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Error results must not carry structured content.

        Strict MCP clients (e.g. TRAE) validate ``structuredContent`` against
        the tool's declared ``outputSchema``. The success-shaped schema
        requires ``duration_seconds`` / ``billing_duration_seconds`` /
        ``artifact``, so an error payload like ``{"error": ...}`` would be
        rejected and mask the real provider error. The error path therefore
        returns text-only content with ``isError=true``.
        """
        mcp: FastMCP = e2e_server.mcp  # type: ignore[attr-defined]

        async def mock_generate(
            self: SeedAudioService,
            request: object,
            *,
            request_id: str | None = None,
        ) -> tuple[SeedAudioProviderResponse, str | None]:
            raise ProviderError(
                NormalizedProviderError(
                    provider="seed-speech",
                    operation="generate_audio",
                    http_status=400,
                    code="INVALID_PARAM",
                    message="text too long",
                    request_id="req-audio-err-001",
                    retryable=False,
                )
            )

        async def mock_close(self: SeedAudioService) -> None:
            return None

        monkeypatch.setattr(SeedAudioService, "generate", mock_generate)
        monkeypatch.setattr(SeedAudioService, "close", mock_close)

        async with Client(mcp) as client:
            result = await _call_background_tool(
                client,
                "seed_audio_generate",
                {"input": {"text_prompt": "a short ambient scene"}},
                raise_on_error=False,
            )

        assert result.is_error
        assert result.structured_content is None
        text = result.content[0].text
        assert "seed-speech generate_audio failed" in text
        assert "code=INVALID_PARAM" in text
        assert "request_id=req-audio-err-001" in text


class TestSeedanceLifecycleE2E:
    """Verify every Seedance task operation works through MCP protocol calls."""

    async def test_create_get_list_and_cancel_task(
        self,
        e2e_server: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mcp: FastMCP = e2e_server.mcp  # type: ignore[attr-defined]
        task = SeedanceTaskResponse(
            id="task-e2e-123",
            model="dreamina-seedance-2-0-260128",
            status="queued",
            created_at=1721400000,
            updated_at=1721400000,
        )

        async def mock_create(self: SeedanceService, request: object) -> tuple[str, str | None]:
            return task.id, "create-request-e2e"

        async def mock_get(
            self: SeedanceService,
            task_id: str,
        ) -> tuple[SeedanceTaskResponse, str | None]:
            assert task_id == task.id
            return task, "get-request-e2e"

        async def mock_list(
            self: SeedanceService, **kwargs: object
        ) -> tuple[SeedanceTaskListResponse, str | None]:
            return SeedanceTaskListResponse(
                data=[task], total=1, has_more=False
            ), "list-request-e2e"

        async def mock_delete(self: SeedanceService, task_id: str) -> str | None:
            assert task_id == task.id
            return "delete-request-e2e"

        async def mock_close(self: SeedanceService) -> None:
            return None

        monkeypatch.setattr(SeedanceService, "create_task", mock_create)
        monkeypatch.setattr(SeedanceService, "get_task", mock_get)
        monkeypatch.setattr(SeedanceService, "list_tasks", mock_list)
        monkeypatch.setattr(SeedanceService, "delete_task", mock_delete)
        monkeypatch.setattr(SeedanceService, "close", mock_close)

        red_pixel = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/"
            "PchI7wAAAABJRU5ErkJggg=="
        )
        async with Client(mcp) as client:
            created = await _call_background_tool(
                client,
                "seedance_create_task",
                {
                    "input": {
                        "prompt": "A cat walks through a garden",
                        "images": [
                            {
                                "kind": "base64",
                                "data": red_pixel,
                                "mime_type": "image/png",
                                "role": "reference_image",
                            }
                        ],
                        "resolution": "480p",
                        "duration": 5,
                    }
                },
            )
            fetched = await client.call_tool(
                "seedance_get_task",
                {"input": {"task_id": task.id, "persist_output": False}},
            )
            async with _foreground_client(mcp) as foreground_client:
                with pytest.raises(
                    ToolError, match="persist_output=true requires task-augmented execution"
                ):
                    await foreground_client.call_tool(
                        "seedance_get_task",
                        {"input": {"task_id": task.id}},
                    )
                compatibility_job_id, compatibility_created = await _call_compatibility_tool(
                    foreground_client,
                    "seedance_create_task",
                    {
                        "input": {
                            "prompt": "A second cat walks through a garden",
                            "resolution": "480p",
                            "duration": 5,
                        }
                    },
                )
                _, compatibility_fetched = await _call_compatibility_tool(
                    foreground_client,
                    "seedance_get_task",
                    {"input": {"task_id": task.id}},
                )
            background_fetched = await _call_background_tool(
                client,
                "seedance_get_task",
                {"input": {"task_id": task.id}},
            )
            listed = await client.call_tool("seedance_list_tasks", {"input": {}})
            cancelled = await client.call_tool(
                "seedance_cancel_or_delete_task",
                {
                    "input": {
                        "task_id": task.id,
                        "mode": "cancel",
                        "expected_status": "queued",
                        "confirm": True,
                    }
                },
            )

        assert not created.is_error
        assert created.structured_content["task_id"] == task.id
        assert compatibility_job_id != task.id
        assert compatibility_created["structured_content"]["task_id"] == task.id
        assert fetched.structured_content["status"] == "queued"
        assert background_fetched.structured_content["status"] == "queued"
        assert compatibility_fetched["structured_content"]["status"] == "queued"
        assert listed.structured_content["total"] == 1
        assert listed.structured_content["tasks"][0]["task_id"] == task.id
        assert cancelled.structured_content["mode"] == "cancel"


class TestVariationToolsE2E:
    """Verify every variation tool serializes successful MCP responses."""

    async def test_seed_audio_variations(
        self, e2e_server: object, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mcp: FastMCP = e2e_server.mcp  # type: ignore[attr-defined]
        audio_b64 = base64.b64encode(b"e2e-variation-audio").decode()

        async def mock_generate(
            self: SeedAudioService,
            request: object,
            *,
            request_id: str | None = None,
        ) -> tuple[SeedAudioProviderResponse, str | None]:
            return (
                SeedAudioProviderResponse(
                    code=0,
                    message="success",
                    audio=audio_b64,
                    duration=1.0,
                    original_duration=1.0,
                ),
                "seed-speech-variation-log",
            )

        async def mock_close(self: SeedAudioService) -> None:
            return None

        monkeypatch.setattr(SeedAudioService, "generate", mock_generate)
        monkeypatch.setattr(SeedAudioService, "close", mock_close)

        async with Client(mcp) as client:
            result = await _call_background_tool(
                client,
                "seed_audio_generate_variations",
                {"input": {"text_prompt": "gentle rain", "variations": 2}},
            )

        assert not result.is_error
        summary = result.structured_content["summary"]
        assert summary["total"] == 2
        assert summary["succeeded"] == 2
        assert all(variation["artifact"] for variation in summary["variations"])

    async def test_seedream_variations(self, e2e_server: object) -> None:
        mcp: FastMCP = e2e_server.mcp  # type: ignore[attr-defined]
        image_b64 = base64.b64encode(b"e2e-variation-image").decode()

        with respx.mock:
            respx.post(f"{ARK_BASE}/images/generations").mock(
                return_value=_mock_seedream_response(img_b64=image_b64)
            )
            async with Client(mcp) as client:
                result = await _call_background_tool(
                    client,
                    "seedream_generate_image_variations",
                    {
                        "input": {
                            "prompt": "A small paper boat",
                            "variations": 2,
                            "base_seed": 10,
                        }
                    },
                )

        assert not result.is_error
        summary = result.structured_content["summary"]
        assert summary["total"] == 2
        assert summary["succeeded"] == 2
        assert [item["seed"] for item in summary["variations"]] == [10, 11]

    async def test_seedance_variations(
        self, e2e_server: object, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mcp: FastMCP = e2e_server.mcp  # type: ignore[attr-defined]

        async def mock_create(self: SeedanceService, request: Any) -> tuple[str, str | None]:
            content = request.content
            prompt = next(item.text for item in content if item.type == "text")
            return f"task-{prompt[-1]}", "seedance-variation-request"

        async def mock_close(self: SeedanceService) -> None:
            return None

        monkeypatch.setattr(SeedanceService, "create_task", mock_create)
        monkeypatch.setattr(SeedanceService, "close", mock_close)
        red_pixel = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/"
            "PchI7wAAAABJRU5ErkJggg=="
        )

        async with Client(mcp) as client:
            result = await _call_background_tool(
                client,
                "seedance_create_task_variations",
                {
                    "input": {
                        "variation_prompts": ["scene 1", "scene 2"],
                        "variations": 2,
                        "images": [
                            {
                                "kind": "base64",
                                "data": red_pixel,
                                "mime_type": "image/png",
                                "role": "reference_image",
                            }
                        ],
                        "resolution": "480p",
                        "duration": 5,
                    }
                },
            )

        assert not result.is_error
        summary = result.structured_content["summary"]
        assert summary["total"] == 2
        assert summary["succeeded"] == 2
        assert {item["task_id"] for item in summary["variations"]} == {"task-1", "task-2"}
