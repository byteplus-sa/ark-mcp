"""Contract tests for the Seedance adapter (video task management).

Tests all four operations (create, get, list, delete), state transitions,
content building, and error propagation.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from ark_mcp.domain.errors import ProviderError
from ark_mcp.providers.modelark.client import ModelArkGateway
from ark_mcp.providers.modelark.schemas import (
    SeedanceContentItem,
)
from ark_mcp.providers.modelark.seedance import SeedanceService

MODELARK_BASE = "https://ark.ap-southeast.bytepluses.com/api/v3"


@pytest.fixture
def service() -> SeedanceService:
    """Create a SeedanceService with a test gateway."""
    gateway = ModelArkGateway(
        api_key="sk-test-key",  # pragma: allowlist secret
        base_url=MODELARK_BASE,
        timeout=10.0,
        connect_timeout=5.0,
    )
    return SeedanceService(gateway=gateway)


class TestSeedanceContentBuilding:
    """Tests for provider content array construction."""

    def test_text_only_content(self) -> None:
        content = SeedanceService.build_content(prompt="a cat playing")
        assert len(content) == 1
        assert content[0].type == "text"
        assert content[0].text == "a cat playing"

    def test_image_with_role(self) -> None:
        content = SeedanceService.build_content(
            prompt="animate this",
            images=[{"url": "https://cdn.example.com/frame.png", "role": "first_frame"}],
        )
        assert len(content) == 2  # text + image
        image_item = content[1]
        assert image_item.type == "image_url"
        assert image_item.role == "first_frame"

    def test_video_reference(self) -> None:
        content = SeedanceService.build_content(
            videos=[{"url": "https://cdn.example.com/ref.mp4"}],
        )
        assert len(content) == 1
        assert content[0].type == "video_url"
        assert content[0].role == "reference_video"

    def test_audio_reference(self) -> None:
        content = SeedanceService.build_content(
            images=[{"url": "https://cdn.example.com/img.png"}],
            audios=[{"url": "https://cdn.example.com/audio.wav"}],
        )
        assert len(content) == 2
        audio_item = content[1]
        assert audio_item.type == "audio_url"
        assert audio_item.role == "reference_audio"

    def test_mixed_content(self) -> None:
        content = SeedanceService.build_content(
            prompt="complex scene",
            images=[
                {"url": "https://cdn.example.com/first.png", "role": "first_frame"},
                {"url": "https://cdn.example.com/ref.png", "role": "reference_image"},
            ],
            videos=[{"url": "https://cdn.example.com/ref.mp4"}],
            audios=[{"url": "https://cdn.example.com/audio.wav"}],
        )
        assert len(content) == 5  # 1 text + 2 images + 1 video + 1 audio

    def test_build_request_passes_omni_reference_task_type(self) -> None:
        content = SeedanceService.build_content(prompt="edit this video")
        request = SeedanceService.build_request(
            model="dreamina-seedance-2-5-260628",
            content=content,
            omni_reference_task_type="edit_video",
        )
        assert request.omni_reference_task_type == "edit_video"

    def test_build_request_omni_reference_task_type_defaults_none(self) -> None:
        content = SeedanceService.build_content(prompt="generate a video")
        request = SeedanceService.build_request(
            model="dreamina-seedance-2-0-260128",
            content=content,
        )
        assert request.omni_reference_task_type is None

    def test_build_request_draft_serializes_flag(self) -> None:
        content = SeedanceService.build_content(prompt="a fox in the snow")
        request = SeedanceService.build_request(
            model="dreamina-seedance-2-5-260628",
            content=content,
            resolution="480p",
            draft=True,
        )
        body = request.model_dump(exclude_none=True)
        assert body["draft"] is True
        assert body["resolution"] == "480p"

    def test_build_request_omits_draft_by_default(self) -> None:
        content = SeedanceService.build_content(prompt="a fox in the snow")
        request = SeedanceService.build_request(
            model="dreamina-seedance-2-5-260628", content=content
        )
        assert "draft" not in request.model_dump(exclude_none=True)

    def test_build_draft_content_references_only_the_draft_task(self) -> None:
        content = SeedanceService.build_draft_content("cgt-draft-1")
        body = [item.model_dump(exclude_none=True) for item in content]
        assert body == [{"type": "draft_task", "draft_task": {"id": "cgt-draft-1"}}]

    def test_prompt_with_video_and_audio(self) -> None:
        content = SeedanceService.build_content(
            prompt="A dancer moving to music",
            videos=[{"url": "https://cdn.example.com/dance.mp4"}],
            audios=[{"url": "https://cdn.example.com/song.wav"}],
        )
        assert len(content) == 3  # 1 text + 1 video + 1 audio
        assert content[0].type == "text"
        assert content[0].text == "A dancer moving to music"
        assert content[1].type == "video_url"
        assert content[1].role == "reference_video"
        assert content[2].type == "audio_url"
        assert content[2].role == "reference_audio"


class TestSeedanceCreateTask:
    """Tests for POST /contents/generations/tasks."""

    @respx.mock
    async def test_create_success(self, service: SeedanceService) -> None:
        respx.post(f"{MODELARK_BASE}/contents/generations/tasks").mock(
            return_value=httpx.Response(
                200,
                json={"id": "task-abc-123"},
                headers={"X-Request-Id": "req-1"},
            )
        )
        content = SeedanceService.build_content(
            prompt="a cat", images=[{"url": "https://cdn.example.com/cat.png"}]
        )
        request = SeedanceService.build_request(
            model="dreamina-seedance-2-0-260128", content=content
        )
        task_id, request_id = await service.create_task(request)
        assert task_id == "task-abc-123"
        assert request_id == "req-1"

    @respx.mock
    async def test_create_validation_error(self, service: SeedanceService) -> None:
        respx.post(f"{MODELARK_BASE}/contents/generations/tasks").mock(
            return_value=httpx.Response(
                400,
                json={"error": {"code": "INVALID_PARAM", "message": "content is empty"}},
            )
        )
        request = SeedanceService.build_request(model="test", content=[])
        with pytest.raises(ProviderError) as exc_info:
            await service.create_task(request)
        assert exc_info.value.http_status == 400

    @respx.mock
    async def test_create_timeout_ambiguous(self, service: SeedanceService) -> None:
        respx.post(f"{MODELARK_BASE}/contents/generations/tasks").mock(
            side_effect=httpx.TimeoutException("timed out")
        )
        request = SeedanceService.build_request(
            model="test", content=[SeedanceContentItem(type="text", text="hi")]
        )
        with pytest.raises(ProviderError) as exc_info:
            await service.create_task(request)
        assert exc_info.value.ambiguous_completion is True


class TestSeedanceGetTask:
    """Tests for GET /contents/generations/tasks/{id}."""

    @respx.mock
    async def test_get_queued_task(self, service: SeedanceService) -> None:
        respx.get(f"{MODELARK_BASE}/contents/generations/tasks/task-1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "task-1",
                    "model": "dreamina-seedance-2-0-260128",
                    "status": "queued",
                    "created_at": 1721400000,
                    "updated_at": 1721400000,
                },
            )
        )
        task, _ = await service.get_task("task-1")
        assert task.id == "task-1"
        assert task.status == "queued"

    @respx.mock
    async def test_get_succeeded_task_with_video(self, service: SeedanceService) -> None:
        respx.get(f"{MODELARK_BASE}/contents/generations/tasks/task-1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "task-1",
                    "model": "dreamina-seedance-2-0-260128",
                    "status": "succeeded",
                    "created_at": 1721400000,
                    "updated_at": 1721400100,
                    "content": {"video_url": "https://cdn.example.com/video.mp4"},
                    "usage": {"completion_tokens": 100, "prompt_tokens": 10},
                },
            )
        )
        task, _ = await service.get_task("task-1")
        assert task.status == "succeeded"
        assert task.video_url == "https://cdn.example.com/video.mp4"
        assert task.usage is not None
        assert task.usage.completion_tokens == 100

    @respx.mock
    async def test_get_failed_task_with_error(self, service: SeedanceService) -> None:
        respx.get(f"{MODELARK_BASE}/contents/generations/tasks/task-1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "task-1",
                    "model": "dreamina-seedance-2-0-260128",
                    "status": "failed",
                    "created_at": 1721400000,
                    "updated_at": 1721400050,
                    "error": {"code": "MODERATION_FAILED", "message": "content rejected"},
                },
            )
        )
        task, _ = await service.get_task("task-1")
        assert task.status == "failed"
        assert task.error is not None
        assert task.error.code == "MODERATION_FAILED"

    @respx.mock
    async def test_get_expired_task(self, service: SeedanceService) -> None:
        respx.get(f"{MODELARK_BASE}/contents/generations/tasks/task-1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "task-1",
                    "model": "dreamina-seedance-2-0-260128",
                    "status": "expired",
                    "created_at": 1721400000,
                    "updated_at": 1721401000,
                },
            )
        )
        task, _ = await service.get_task("task-1")
        assert task.status == "expired"

    @respx.mock
    async def test_get_task_with_last_frame(self, service: SeedanceService) -> None:
        respx.get(f"{MODELARK_BASE}/contents/generations/tasks/task-1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "task-1",
                    "model": "dreamina-seedance-2-0-260128",
                    "status": "succeeded",
                    "created_at": 1721400000,
                    "updated_at": 1721400100,
                    "content": {
                        "video_url": "https://cdn.example.com/video.mp4",
                        "last_frame_url": "https://cdn.example.com/last_frame.jpg",
                    },
                },
            )
        )
        task, _ = await service.get_task("task-1")
        assert task.video_url == "https://cdn.example.com/video.mp4"
        assert task.last_frame_url == "https://cdn.example.com/last_frame.jpg"

    @respx.mock
    async def test_get_not_found(self, service: SeedanceService) -> None:
        respx.get(f"{MODELARK_BASE}/contents/generations/tasks/nonexistent").mock(
            return_value=httpx.Response(
                404,
                json={"error": {"code": "NOT_FOUND", "message": "task not found"}},
            )
        )
        with pytest.raises(ProviderError) as exc_info:
            await service.get_task("nonexistent")
        assert exc_info.value.http_status == 404


class TestSeedanceListTasks:
    """Tests for GET /contents/generations/tasks."""

    @respx.mock
    async def test_list_success(self, service: SeedanceService) -> None:
        respx.get(f"{MODELARK_BASE}/contents/generations/tasks").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "task-1",
                            "model": "dreamina-seedance-2-0-260128",
                            "status": "succeeded",
                            "created_at": 1721400000,
                            "updated_at": 1721400100,
                        },
                        {
                            "id": "task-2",
                            "model": "dreamina-seedance-2-0-260128",
                            "status": "running",
                            "created_at": 1721400200,
                            "updated_at": 1721400300,
                        },
                    ],
                    "total": 2,
                    "has_more": False,
                },
            )
        )
        page, _ = await service.list_tasks(page=1, page_size=20)
        assert len(page.data) == 2
        assert page.total == 2
        assert page.has_more is False

    @respx.mock
    async def test_list_accepts_current_items_response(self, service: SeedanceService) -> None:
        respx.get(f"{MODELARK_BASE}/contents/generations/tasks").mock(
            return_value=httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "task-1",
                            "model": "dreamina-seedance-2-0-260128",
                            "status": "succeeded",
                        }
                    ],
                    "total": 1,
                },
            )
        )
        page, _ = await service.list_tasks()
        assert [task.id for task in page.data] == ["task-1"]

    @respx.mock
    async def test_list_with_status_filter(self, service: SeedanceService) -> None:
        route = respx.get(f"{MODELARK_BASE}/contents/generations/tasks").mock(
            return_value=httpx.Response(
                200,
                json={"data": [], "total": 0},
            )
        )
        await service.list_tasks(status="succeeded")
        assert "filter.status=succeeded" in str(route.calls.last.request.url)

    @respx.mock
    async def test_list_with_task_id_filters(self, service: SeedanceService) -> None:
        route = respx.get(f"{MODELARK_BASE}/contents/generations/tasks").mock(
            return_value=httpx.Response(200, json={"data": [], "total": 0})
        )
        await service.list_tasks(task_ids=["task-1", "task-2"])
        request_url = str(route.calls.last.request.url)
        assert "filter.task_ids=task-1" in request_url
        assert "filter.task_ids=task-2" in request_url

    @respx.mock
    async def test_list_pagination_params(self, service: SeedanceService) -> None:
        route = respx.get(f"{MODELARK_BASE}/contents/generations/tasks").mock(
            return_value=httpx.Response(
                200,
                json={"data": [], "total": 0},
            )
        )
        await service.list_tasks(page=3, page_size=50)
        request_url = str(route.calls.last.request.url)
        assert "page_num=3" in request_url
        assert "page_size=50" in request_url


class TestSeedanceDeleteTask:
    """Tests for DELETE /contents/generations/tasks/{id}."""

    @respx.mock
    async def test_delete_success(self, service: SeedanceService) -> None:
        respx.delete(f"{MODELARK_BASE}/contents/generations/tasks/task-1").mock(
            return_value=httpx.Response(
                204,
                headers={"X-Request-Id": "req-del"},
            )
        )
        request_id = await service.delete_task("task-1")
        assert request_id == "req-del"

    @respx.mock
    async def test_delete_not_found(self, service: SeedanceService) -> None:
        respx.delete(f"{MODELARK_BASE}/contents/generations/tasks/nonexistent").mock(
            return_value=httpx.Response(
                404,
                json={"error": {"code": "NOT_FOUND", "message": "task not found"}},
            )
        )
        with pytest.raises(ProviderError) as exc_info:
            await service.delete_task("nonexistent")
        assert exc_info.value.http_status == 404

    @respx.mock
    async def test_delete_timeout_ambiguous(self, service: SeedanceService) -> None:
        respx.delete(f"{MODELARK_BASE}/contents/generations/tasks/task-1").mock(
            side_effect=httpx.TimeoutException("timed out")
        )
        with pytest.raises(ProviderError) as exc_info:
            await service.delete_task("task-1")
        assert exc_info.value.code == "TIMEOUT"


class TestSeedanceTaskSummary:
    """Tests for task summary and usage extraction."""

    def test_to_task_summary(self) -> None:
        from ark_mcp.providers.modelark.schemas import SeedanceTaskResponse

        task = SeedanceTaskResponse(
            id="task-1",
            model="dreamina-seedance-2-0-260128",
            status="succeeded",
            created_at=1721400000,
            updated_at=1721400100,
        )
        summary = SeedanceService.to_task_summary(task)
        assert summary.task_id == "task-1"
        assert summary.status == "succeeded"
        assert summary.model == "dreamina-seedance-2-0-260128"

    def test_extract_usage_none(self) -> None:
        from ark_mcp.providers.modelark.schemas import SeedanceTaskResponse

        task = SeedanceTaskResponse(
            id="task-1",
            model="test",
            status="succeeded",
        )
        assert SeedanceService.extract_usage(task) is None

    def test_extract_usage_present(self) -> None:
        from ark_mcp.providers.modelark.schemas import (
            SeedanceGenerationUsage,
            SeedanceTaskResponse,
        )

        task = SeedanceTaskResponse(
            id="task-1",
            model="test",
            status="succeeded",
            usage=SeedanceGenerationUsage(completion_tokens=50, prompt_tokens=5),
        )
        usage = SeedanceService.extract_usage(task)
        assert usage is not None
        assert usage.completion_tokens == 50


class TestSeedanceInvalidResponse:
    """2xx responses with non-JSON bodies must fail as INVALID_RESPONSE."""

    @respx.mock
    async def test_create_non_json_success_body(self, service: SeedanceService) -> None:
        respx.post(f"{MODELARK_BASE}/contents/generations/tasks").mock(
            return_value=httpx.Response(200, content=b"<html>not json</html>")
        )
        request = SeedanceService.build_request(
            model="test", content=[SeedanceContentItem(type="text", text="hi")]
        )
        with pytest.raises(ProviderError) as exc_info:
            await service.create_task(request)
        assert exc_info.value.code == "INVALID_RESPONSE"
        assert exc_info.value.http_status == 200

    @respx.mock
    async def test_get_empty_success_body(self, service: SeedanceService) -> None:
        respx.get(f"{MODELARK_BASE}/contents/generations/tasks/task-1").mock(
            return_value=httpx.Response(200, content=b"")
        )
        with pytest.raises(ProviderError) as exc_info:
            await service.get_task("task-1")
        assert exc_info.value.code == "INVALID_RESPONSE"

    @respx.mock
    async def test_list_non_json_success_body(self, service: SeedanceService) -> None:
        respx.get(f"{MODELARK_BASE}/contents/generations/tasks").mock(
            return_value=httpx.Response(200, content=b"not json")
        )
        with pytest.raises(ProviderError) as exc_info:
            await service.list_tasks()
        assert exc_info.value.code == "INVALID_RESPONSE"


class TestSeedanceUnknownStatus:
    """Unknown provider status strings map to SeedanceTaskStatus.UNKNOWN."""

    def test_unknown_status_enum_member(self) -> None:
        from ark_mcp.domain.models import SeedanceTaskStatus

        assert SeedanceTaskStatus("invented_status") is SeedanceTaskStatus.UNKNOWN

    def test_to_task_summary_unknown_status(self) -> None:
        from ark_mcp.domain.models import SeedanceTaskStatus
        from ark_mcp.providers.modelark.schemas import SeedanceTaskResponse

        task = SeedanceTaskResponse(
            id="task-1",
            model="dreamina-seedance-2-0-260128",
            status="invented_status",
            created_at=1721400000,
            updated_at=1721400000,
        )
        summary = SeedanceService.to_task_summary(task)
        assert summary.status is SeedanceTaskStatus.UNKNOWN

    def test_get_task_output_validates_with_unknown_status(self) -> None:
        from ark_mcp.domain.models import SeedanceTaskStatus
        from ark_mcp.tools.seedance_get_task import SeedanceTaskOutput

        output = SeedanceTaskOutput(
            task_id="task-1",
            model="dreamina-seedance-2-0-260128",
            status="invented_status",
            created_at="2026-06-02T07:36:15+00:00",
            updated_at="2026-06-02T07:36:15+00:00",
        )
        assert output.status is SeedanceTaskStatus.UNKNOWN

    def test_list_tasks_output_validates_with_unknown_status(self) -> None:
        from ark_mcp.domain.models import SeedanceTaskStatus
        from ark_mcp.tools.seedance_list_tasks import SeedanceTaskPage

        page = SeedanceTaskPage(
            tasks=[
                {
                    "task_id": "task-1",
                    "model": "dreamina-seedance-2-0-260128",
                    "status": "invented_status",
                    "created_at": "2026-06-02T07:36:15+00:00",
                    "updated_at": "2026-06-02T07:36:15+00:00",
                }
            ],
            total=1,
            page=1,
            page_size=20,
        )
        assert page.tasks[0].status is SeedanceTaskStatus.UNKNOWN
