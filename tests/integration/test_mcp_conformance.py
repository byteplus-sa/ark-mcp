"""MCP protocol conformance tests.

Verifies tool discovery, inputSchema/outputSchema auto-generation,
ToolAnnotations propagation, resource template registration, and
conditional tool registration based on credentials.

These tests exercise the FastMCP layer directly — no provider calls.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from ark_mcp.background_jobs import BACKGROUND_TOOL_SPECS
from ark_mcp.config.env import Settings, get_settings
from ark_mcp.server import create_server


@pytest.fixture
def configured_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Set test env vars and re-register tools with fake credentials."""
    monkeypatch.setenv("BYTEPLUS_MODELARK_API_KEY", "sk-test")
    monkeypatch.setenv("BYTEPLUS_SEED_SPEECH_API_KEY", "sk-test")
    monkeypatch.setenv("BYTEPLUS_VOD_MEDIAKIT_API_KEY", "test-mediakit-key")
    monkeypatch.setenv("BYTEPLUS_MODELARK_3D_ENABLED", "false")
    monkeypatch.setenv("TOS_ACCESS_KEY", "ak-test-tos")
    monkeypatch.setenv("TOS_SECRET_KEY", "sk-test-tos")
    monkeypatch.setenv("TOS_BUCKET", "test-bucket")

    # Clear cached settings.
    get_settings.cache_clear()

    yield SimpleNamespace(mcp=create_server(get_settings()))

    get_settings.cache_clear()


@pytest.fixture
def no_creds_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configure server with no API keys set."""
    monkeypatch.delenv("BYTEPLUS_MODELARK_API_KEY", raising=False)
    monkeypatch.delenv("BYTEPLUS_SEED_SPEECH_API_KEY", raising=False)
    monkeypatch.setenv("BYTEPLUS_MODELARK_API_KEY", "")
    monkeypatch.setenv("BYTEPLUS_SEED_SPEECH_API_KEY", "")

    get_settings.cache_clear()

    yield SimpleNamespace(
        mcp=create_server(
            Settings(
                _env_file=None,
                BYTEPLUS_MODELARK_API_KEY="",
                BYTEPLUS_SEED_SPEECH_API_KEY="",
                BYTEPLUS_VOD_MEDIAKIT_API_KEY="",
            )
        )
    )

    get_settings.cache_clear()


@pytest.fixture
def s3_only_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configure server with S3-only object storage creds."""
    monkeypatch.setenv("BYTEPLUS_MODELARK_API_KEY", "sk-test")
    monkeypatch.setenv("BYTEPLUS_SEED_SPEECH_API_KEY", "sk-test")
    monkeypatch.setenv("S3_ACCESS_KEY", "ak-s3-test")
    monkeypatch.setenv("S3_SECRET_KEY", "sk-s3-test")
    monkeypatch.setenv("S3_BUCKET", "test-s3-bucket")
    monkeypatch.setenv("OBJECT_STORAGE_BACKEND", "s3")

    get_settings.cache_clear()

    yield SimpleNamespace(mcp=create_server(get_settings()))

    get_settings.cache_clear()


@pytest.fixture
def seed3d_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BYTEPLUS_MODELARK_API_KEY", "sk-test")
    monkeypatch.setenv("BYTEPLUS_MODELARK_3D_ENABLED", "true")

    get_settings.cache_clear()

    yield SimpleNamespace(mcp=create_server(get_settings()))

    get_settings.cache_clear()


class TestToolDiscovery:
    """Verify configured tools are discoverable when credentials are set."""

    async def test_all_tools_registered(self, configured_server: None) -> None:
        server = configured_server
        tools = await server.mcp.list_tools()
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
            "seedream_edit_image",
            "seedream_generate_image",
            "seedream_generate_image_variations",
            "seedance_create_task",
            "seedance_create_task_variations",
            "seedance_2_5_create_task",
            "seedance_2_5_create_task_variations",
            "seedance_get_task",
            "seedance_list_tasks",
            "seedance_cancel_or_delete_task",
            "seed_understand",
            "speech_to_text",
            "media_upload",
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

    async def test_long_running_tools_require_background_execution(
        self, configured_server: None
    ) -> None:
        server = configured_server
        tools = await server.mcp.list_tools()
        tools_by_name = {tool.name: tool for tool in tools}
        required_task_names = {
            "media_upload",
            "seed_audio_generate",
            "seed_audio_generate_variations",
            "speech_to_text",
            "seed_understand",
            "seedream_generate_image",
            "seedream_edit_image",
            "seedream_generate_image_variations",
            "seedance_create_task",
            "seedance_create_task_variations",
            "seedance_2_5_create_task",
            "seedance_2_5_create_task_variations",
            "vod_enhance_video",
            "vod_transcode_video",
            "vod_separate_audio",
            "vod_add_subtitles",
            "vod_remove_subtitles",
        }
        assert required_task_names <= tools_by_name.keys()

        for name in required_task_names:
            tool = tools_by_name[name]
            assert tool.task_config.mode == "required"
            assert tool.task_config.poll_interval == timedelta(seconds=2)
            mcp_tool = tool.to_mcp_tool()
            assert mcp_tool.execution is not None
            assert mcp_tool.execution.task_support == "required"

    async def test_completed_media_retrieval_tools_support_optional_background_execution(
        self, configured_server: None
    ) -> None:
        server = configured_server
        tools = await server.mcp.list_tools()
        tools_by_name = {tool.name: tool for tool in tools}
        optional_task_names = {
            "seedance_get_task",
            "vod_get_enhancement_task",
            "vod_get_transcode_task",
            "vod_get_audio_separation",
            "vod_get_subtitle_addition_task",
            "vod_get_subtitle_removal_task",
        }
        assert optional_task_names <= tools_by_name.keys()

        for name in optional_task_names:
            tool = tools_by_name[name]
            assert tool.task_config.mode == "optional"
            assert tool.task_config.poll_interval == timedelta(seconds=2)
            mcp_tool = tool.to_mcp_tool()
            assert mcp_tool.execution is not None
            assert mcp_tool.execution.task_support == "optional"

    async def test_short_control_plane_tools_remain_foreground(
        self, configured_server: None
    ) -> None:
        server = configured_server
        tools = await server.mcp.list_tools()
        tools_by_name = {tool.name: tool for tool in tools}
        foreground_names = {
            "ark_job_cancel",
            "ark_job_capabilities",
            "ark_job_get",
            "ark_job_submit",
            "seed_media_export_artifact",
            "seed_media_get_artifact",
            "media_presign",
            "media_presign_batch",
            "seedance_list_tasks",
            "seedance_cancel_or_delete_task",
        }
        assert foreground_names <= tools_by_name.keys()

        for name in foreground_names:
            tool = tools_by_name[name]
            assert tool.task_config.mode == "forbidden"
            mcp_tool = tool.to_mcp_tool()
            assert mcp_tool.execution is None

    async def test_seed3d_create_and_retrieval_task_modes(self, seed3d_server: None) -> None:
        tools = await seed3d_server.mcp.list_tools()
        tools_by_name = {tool.name: tool for tool in tools}

        for name in {"hyper3d_create_task", "hitem3d_create_task"}:
            tool = tools_by_name[name]
            assert tool.task_config.mode == "required"
            assert tool.task_config.poll_interval == timedelta(seconds=2)

        for name in {"hyper3d_get_task", "hitem3d_get_task"}:
            tool = tools_by_name[name]
            assert tool.task_config.mode == "optional"
            assert tool.task_config.poll_interval == timedelta(seconds=2)

        for name in {
            "hyper3d_list_tasks",
            "hyper3d_cancel_or_delete_task",
            "hitem3d_list_tasks",
            "hitem3d_cancel_or_delete_task",
        }:
            assert tools_by_name[name].task_config.mode == "forbidden"

    async def test_compatibility_registry_covers_all_task_enabled_tools(
        self,
        configured_server: None,
        seed3d_server: None,
    ) -> None:
        configured_tools = await configured_server.mcp.list_tools()
        seed3d_tools = await seed3d_server.mcp.list_tools()
        task_enabled_names = {
            tool.name
            for tool in [*configured_tools, *seed3d_tools]
            if tool.task_config.mode in {"required", "optional"}
        }

        assert task_enabled_names == BACKGROUND_TOOL_SPECS.keys()

    async def test_background_job_tool_schemas_describe_public_fields(
        self,
        configured_server: None,
    ) -> None:
        tools = {tool.name: tool.to_mcp_tool() for tool in await configured_server.mcp.list_tools()}
        for tool_name in {
            "ark_job_capabilities",
            "ark_job_submit",
            "ark_job_get",
            "ark_job_cancel",
        }:
            tool = tools[tool_name]
            assert tool.description
            assert tool.output_schema is not None
            assert all(
                property_schema.get("description")
                for property_schema in tool.output_schema["properties"].values()
            )

        for tool_name in {"ark_job_submit", "ark_job_get", "ark_job_cancel"}:
            input_schema = tools[tool_name].input_schema
            input_property = input_schema["properties"]["input"]
            if reference := input_property.get("$ref"):
                model_schema = input_schema["$defs"][reference.rsplit("/", 1)[-1]]
            else:
                model_schema = input_property
            assert all(
                property_schema.get("description")
                for property_schema in model_schema["properties"].values()
            )

    async def test_vod_mediakit_tool_not_registered_without_its_key(
        self, no_creds_server: None
    ) -> None:
        tools = await no_creds_server.mcp.list_tools()
        names = {tool.name for tool in tools}
        assert "vod_enhance_video" not in names
        assert "vod_get_enhancement_task" not in names
        assert "vod_transcode_video" not in names
        assert "vod_get_transcode_task" not in names
        assert "vod_separate_audio" not in names
        assert "vod_get_audio_separation" not in names
        assert "vod_add_subtitles" not in names
        assert "vod_get_subtitle_addition_task" not in names
        assert "vod_remove_subtitles" not in names
        assert "vod_get_subtitle_removal_task" not in names

    async def test_media_upload_registered_with_s3_only(self, s3_only_server: None) -> None:
        server = s3_only_server
        tools = await server.mcp.list_tools()
        tool_names = {t.name for t in tools}
        assert "media_upload" in tool_names
        assert "media_presign" in tool_names


class TestToolAnnotations:
    """Verify ToolAnnotations are correctly propagated."""

    async def test_seed_audio_annotations(self, configured_server: None) -> None:
        server = configured_server
        tools = await server.mcp.list_tools()
        tool = next(t for t in tools if t.name == "seed_audio_generate")
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is False
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.idempotent_hint is False
        assert tool.annotations.open_world_hint is True

    async def test_seedream_annotations(self, configured_server: None) -> None:
        server = configured_server
        tools = await server.mcp.list_tools()
        tool = next(t for t in tools if t.name == "seedream_generate_image")
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is False
        assert tool.annotations.open_world_hint is True

    async def test_seedance_create_annotations(self, configured_server: None) -> None:
        server = configured_server
        tools = await server.mcp.list_tools()
        tool = next(t for t in tools if t.name == "seedance_create_task")
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is False
        assert tool.annotations.open_world_hint is True

    async def test_seedance_get_readonly(self, configured_server: None) -> None:
        server = configured_server
        tools = await server.mcp.list_tools()
        tool = next(t for t in tools if t.name == "seedance_get_task")
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.idempotent_hint is True

    async def test_seedance_list_readonly(self, configured_server: None) -> None:
        server = configured_server
        tools = await server.mcp.list_tools()
        tool = next(t for t in tools if t.name == "seedance_list_tasks")
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.idempotent_hint is True

    async def test_seedance_cancel_destructive(self, configured_server: None) -> None:
        server = configured_server
        tools = await server.mcp.list_tools()
        tool = next(t for t in tools if t.name == "seedance_cancel_or_delete_task")
        assert tool.annotations is not None
        assert tool.annotations.destructive_hint is True
        assert tool.annotations.read_only_hint is False

    async def test_vod_enhance_annotations(self, configured_server: None) -> None:
        tools = await configured_server.mcp.list_tools()
        tool = next(t for t in tools if t.name == "vod_enhance_video")
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is False
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.idempotent_hint is False
        assert tool.annotations.open_world_hint is True

    async def test_vod_transcode_submit_annotations(self, configured_server: None) -> None:
        tools = await configured_server.mcp.list_tools()
        tool = next(t for t in tools if t.name == "vod_transcode_video")
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is False
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.idempotent_hint is False
        assert tool.annotations.open_world_hint is True

    async def test_vod_get_enhancement_annotations(self, configured_server: None) -> None:
        tools = await configured_server.mcp.list_tools()
        tool = next(t for t in tools if t.name == "vod_get_enhancement_task")
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.idempotent_hint is True
        assert tool.annotations.open_world_hint is False

    async def test_vod_get_transcode_annotations(self, configured_server: None) -> None:
        tools = await configured_server.mcp.list_tools()
        tool = next(t for t in tools if t.name == "vod_get_transcode_task")
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.idempotent_hint is True
        assert tool.annotations.open_world_hint is False

    async def test_vod_separate_audio_annotations(self, configured_server: None) -> None:
        tools = await configured_server.mcp.list_tools()
        tool = next(t for t in tools if t.name == "vod_separate_audio")
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is False
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.idempotent_hint is False
        assert tool.annotations.open_world_hint is True

    async def test_vod_get_audio_separation_annotations(self, configured_server: None) -> None:
        tools = await configured_server.mcp.list_tools()
        tool = next(t for t in tools if t.name == "vod_get_audio_separation")
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.idempotent_hint is True
        assert tool.annotations.open_world_hint is False

    @pytest.mark.parametrize("tool_name", ["vod_add_subtitles", "vod_remove_subtitles"])
    async def test_vod_subtitle_submit_annotations(
        self, configured_server: None, tool_name: str
    ) -> None:
        tools = await configured_server.mcp.list_tools()
        tool = next(item for item in tools if item.name == tool_name)
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is False
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.idempotent_hint is False
        assert tool.annotations.open_world_hint is True

    @pytest.mark.parametrize(
        "tool_name",
        ["vod_get_subtitle_addition_task", "vod_get_subtitle_removal_task"],
    )
    async def test_vod_subtitle_poll_annotations(
        self, configured_server: None, tool_name: str
    ) -> None:
        tools = await configured_server.mcp.list_tools()
        tool = next(item for item in tools if item.name == tool_name)
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.idempotent_hint is True
        assert tool.annotations.open_world_hint is False


class TestInputSchemas:
    """Verify inputSchema is auto-generated for each tool."""

    async def test_seed_audio_input_schema(self, configured_server: None) -> None:
        server = configured_server
        tools = await server.mcp.list_tools()
        tool = next(t for t in tools if t.name == "seed_audio_generate")
        schema = tool.parameters
        assert schema is not None
        assert "properties" in schema
        assert "text_prompt" in schema["properties"]["input"]["properties"]
        assert schema["properties"]["input"]["required"] == ["text_prompt"]

    async def test_seedream_input_schema(self, configured_server: None) -> None:
        server = configured_server
        tools = await server.mcp.list_tools()
        tool = next(t for t in tools if t.name == "seedream_generate_image")
        schema = tool.parameters
        assert schema is not None
        assert "prompt" in schema["properties"]["input"]["properties"]
        assert schema["properties"]["input"]["required"] == ["prompt"]

    async def test_seedance_create_input_schema(self, configured_server: None) -> None:
        server = configured_server
        tools = await server.mcp.list_tools()
        tool = next(t for t in tools if t.name == "seedance_create_task")
        schema = tool.parameters
        assert schema is not None
        assert "properties" in schema

    async def test_seedance_get_input_schema(self, configured_server: None) -> None:
        server = configured_server
        tools = await server.mcp.list_tools()
        tool = next(t for t in tools if t.name == "seedance_get_task")
        schema = tool.parameters
        assert schema is not None
        assert "task_id" in schema["properties"]["input"]["properties"]
        assert schema["properties"]["input"]["required"] == ["task_id"]

    async def test_seedance_cancel_input_schema(self, configured_server: None) -> None:
        server = configured_server
        tools = await server.mcp.list_tools()
        tool = next(t for t in tools if t.name == "seedance_cancel_or_delete_task")
        schema = tool.parameters
        assert schema is not None
        input_props = schema["properties"]["input"]["properties"]
        assert "task_id" in input_props
        assert "mode" in input_props
        assert "expected_status" in input_props
        assert "confirm" in input_props

    async def test_vod_enhance_schema_is_self_describing(self, configured_server: None) -> None:
        tools = await configured_server.mcp.list_tools()
        tool = next(t for t in tools if t.name == "vod_enhance_video")
        assert tool.description is not None
        assert "accepted response contains a task ID without an output URL" in tool.description
        assert "vod_get_enhancement_task" in tool.description
        assert "always returned" not in tool.description
        input_schema = tool.parameters["properties"]["input"]
        assert input_schema["required"] == ["video_url"]
        assert all("description" in field for field in input_schema["properties"].values())
        assert tool.output_schema is not None
        task_description = tool.output_schema["properties"]["task_id"]["description"]
        assert "vod_get_enhancement_task" in task_description
        assert "synchronous result" not in task_description
        assert all(
            "description" in field or "$ref" in field
            for field in tool.output_schema["properties"].values()
        )

    async def test_vod_get_enhancement_schema_is_self_describing(
        self, configured_server: None
    ) -> None:
        tools = await configured_server.mcp.list_tools()
        tool = next(t for t in tools if t.name == "vod_get_enhancement_task")
        assert tool.description is not None
        input_schema = tool.parameters["properties"]["input"]
        assert input_schema["required"] == ["task_id"]
        assert all("description" in field for field in input_schema["properties"].values())
        assert tool.output_schema is not None
        assert tool.output_schema["properties"]["status"]["description"]
        assert all(
            "description" in field or "$ref" in field
            for field in tool.output_schema["properties"].values()
        )

    async def test_vod_transcode_submit_schema_is_self_describing(
        self, configured_server: None
    ) -> None:
        tools = await configured_server.mcp.list_tools()
        tool = next(t for t in tools if t.name == "vod_transcode_video")
        assert tool.description is not None
        assert "vod_get_transcode_task" in tool.description
        input_schema = tool.parameters["properties"]["input"]
        assert input_schema["required"] == ["video_url"]
        assert all("description" in field for field in input_schema["properties"].values())
        assert tool.output_schema is not None
        assert tool.output_schema["properties"]["task_id"]["description"]
        assert all(
            "description" in field or "$ref" in field
            for field in tool.output_schema["properties"].values()
        )

    async def test_vod_get_transcode_schema_is_self_describing(
        self, configured_server: None
    ) -> None:
        tools = await configured_server.mcp.list_tools()
        tool = next(t for t in tools if t.name == "vod_get_transcode_task")
        assert tool.description is not None
        input_schema = tool.parameters["properties"]["input"]
        assert input_schema["required"] == ["task_id"]
        assert all("description" in field for field in input_schema["properties"].values())
        assert tool.output_schema is not None
        assert tool.output_schema["properties"]["status"]["description"]
        assert all(
            "description" in field or "$ref" in field
            for field in tool.output_schema["properties"].values()
        )

    async def test_vod_separate_audio_schema_is_self_describing(
        self, configured_server: None
    ) -> None:
        tools = await configured_server.mcp.list_tools()
        tool = next(t for t in tools if t.name == "vod_separate_audio")
        assert tool.description is not None
        assert "vod_get_audio_separation" in tool.description
        input_schema = tool.parameters["properties"]["input"]
        assert "audio_url" in input_schema["properties"]
        assert "video_url" in input_schema["properties"]
        assert "scene" in input_schema["properties"]
        assert "output_format" in input_schema["properties"]
        assert "required" not in input_schema
        assert all("description" in field for field in input_schema["properties"].values())
        assert tool.output_schema is not None
        assert tool.output_schema["properties"]["task_id"]["description"]
        assert all(
            "description" in field or "$ref" in field
            for field in tool.output_schema["properties"].values()
        )

    async def test_vod_get_audio_separation_schema_is_self_describing(
        self, configured_server: None
    ) -> None:
        tools = await configured_server.mcp.list_tools()
        tool = next(t for t in tools if t.name == "vod_get_audio_separation")
        assert tool.description is not None
        input_schema = tool.parameters["properties"]["input"]
        assert input_schema["required"] == ["task_id"]
        assert all("description" in field for field in input_schema["properties"].values())
        assert tool.output_schema is not None
        assert tool.output_schema["properties"]["status"]["description"]
        assert all(
            "description" in field or "$ref" in field
            for field in tool.output_schema["properties"].values()
        )

    @pytest.mark.parametrize("tool_name", ["vod_add_subtitles", "vod_remove_subtitles"])
    async def test_vod_subtitle_submit_schema_is_self_describing(
        self, configured_server: None, tool_name: str
    ) -> None:
        tools = await configured_server.mcp.list_tools()
        tool = next(item for item in tools if item.name == tool_name)
        assert tool.description
        input_schema = tool.parameters["properties"]["input"]
        assert input_schema["required"] == ["video_url"]
        assert all("description" in field for field in input_schema["properties"].values())
        assert tool.output_schema is not None
        assert all(
            "description" in field or "$ref" in field
            for field in tool.output_schema["properties"].values()
        )

    async def test_vod_add_subtitles_cue_schema_is_self_describing(
        self, configured_server: None
    ) -> None:
        tools = await configured_server.mcp.list_tools()
        tool = next(item for item in tools if item.name == "vod_add_subtitles")
        input_schema = tool.parameters["properties"]["input"]
        cue_schema = input_schema["properties"]["subtitles"]["anyOf"][0]["items"]
        assert all("description" in field for field in cue_schema["properties"].values())

    @pytest.mark.parametrize(
        "tool_name",
        ["vod_get_subtitle_addition_task", "vod_get_subtitle_removal_task"],
    )
    async def test_vod_subtitle_poll_schema_is_self_describing(
        self, configured_server: None, tool_name: str
    ) -> None:
        tools = await configured_server.mcp.list_tools()
        tool = next(item for item in tools if item.name == tool_name)
        assert tool.description
        input_schema = tool.parameters["properties"]["input"]
        assert input_schema["required"] == ["task_id"]
        assert all("description" in field for field in input_schema["properties"].values())
        assert tool.output_schema is not None
        assert all(
            "description" in field or "$ref" in field
            for field in tool.output_schema["properties"].values()
        )


class TestOutputSchemas:
    """Verify outputSchema is auto-generated for each tool."""

    async def test_seedream_output_schema(self, configured_server: None) -> None:
        server = configured_server
        tools = await server.mcp.list_tools()
        tool = next(t for t in tools if t.name == "seedream_generate_image")
        schema = tool.output_schema
        assert schema is not None
        assert "properties" in schema
        assert "artifacts" in schema["properties"]
        assert "model" in schema["properties"]

    async def test_seedance_create_output_schema(self, configured_server: None) -> None:
        server = configured_server
        tools = await server.mcp.list_tools()
        tool = next(t for t in tools if t.name == "seedance_create_task")
        schema = tool.output_schema
        assert schema is not None
        assert "task_id" in schema["properties"]


class TestVariationToolAnnotations:
    """Verify ToolAnnotations for the three variation tools."""

    async def test_seedream_variations_annotations(self, configured_server: None) -> None:
        server = configured_server
        tools = await server.mcp.list_tools()
        tool = next(t for t in tools if t.name == "seedream_generate_image_variations")
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is False
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.open_world_hint is True

    async def test_seed_audio_variations_annotations(self, configured_server: None) -> None:
        server = configured_server
        tools = await server.mcp.list_tools()
        tool = next(t for t in tools if t.name == "seed_audio_generate_variations")
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is False
        assert tool.annotations.open_world_hint is True

    async def test_seedance_variations_annotations(self, configured_server: None) -> None:
        server = configured_server
        tools = await server.mcp.list_tools()
        tool = next(t for t in tools if t.name == "seedance_create_task_variations")
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is False
        assert tool.annotations.open_world_hint is True


class TestVariationInputSchemas:
    """Verify inputSchema is auto-generated for variation tools."""

    async def test_seedream_variations_input_schema(self, configured_server: None) -> None:
        server = configured_server
        tools = await server.mcp.list_tools()
        tool = next(t for t in tools if t.name == "seedream_generate_image_variations")
        schema = tool.parameters
        assert schema is not None
        input_props = schema["properties"]["input"]["properties"]
        assert "prompt" in input_props
        assert "variations" in input_props
        assert "base_seed" in input_props

    async def test_seed_audio_variations_input_schema(self, configured_server: None) -> None:
        server = configured_server
        tools = await server.mcp.list_tools()
        tool = next(t for t in tools if t.name == "seed_audio_generate_variations")
        schema = tool.parameters
        assert schema is not None
        input_props = schema["properties"]["input"]["properties"]
        assert "text_prompt" in input_props
        assert "variations" in input_props

    async def test_seedance_variations_input_schema(self, configured_server: None) -> None:
        server = configured_server
        tools = await server.mcp.list_tools()
        tool = next(t for t in tools if t.name == "seedance_create_task_variations")
        schema = tool.parameters
        assert schema is not None
        input_props = schema["properties"]["input"]["properties"]
        assert "variations" in input_props
        assert "variation_prompts" in input_props


class TestVariationOutputSchemas:
    """Verify outputSchema is auto-generated for variation tools."""

    async def test_seedream_variations_output_schema(self, configured_server: None) -> None:
        server = configured_server
        tools = await server.mcp.list_tools()
        tool = next(t for t in tools if t.name == "seedream_generate_image_variations")
        schema = tool.output_schema
        assert schema is not None
        assert "summary" in schema["properties"]

    async def test_seed_audio_variations_output_schema(self, configured_server: None) -> None:
        server = configured_server
        tools = await server.mcp.list_tools()
        tool = next(t for t in tools if t.name == "seed_audio_generate_variations")
        schema = tool.output_schema
        assert schema is not None
        assert "summary" in schema["properties"]

    async def test_seedance_variations_output_schema(self, configured_server: None) -> None:
        server = configured_server
        tools = await server.mcp.list_tools()
        tool = next(t for t in tools if t.name == "seedance_create_task_variations")
        schema = tool.output_schema
        assert schema is not None
        assert "summary" in schema["properties"]
        assert "recommended_poll_after_ms" in schema["properties"]


class TestResourceTemplate:
    """Verify the seed-media artifact resource template is registered."""

    async def test_resource_template_registered(self, configured_server: None) -> None:
        server = configured_server
        templates = await server.mcp.list_resource_templates()
        assert len(templates) >= 1
        artifact_tmpl = next(t for t in templates if "artifacts" in t.uri_template)
        assert "artifact_id" in artifact_tmpl.uri_template
        assert artifact_tmpl.name == "get_artifact"

    async def test_health_resource_registered(self, configured_server: None) -> None:
        server = configured_server
        resources = await server.mcp.list_resources()
        # Health resource is a static resource, not a template.
        assert len(resources) >= 1
