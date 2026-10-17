"""Contract tests for the shared ModelArk generation task reads (get, list, delete)."""

from __future__ import annotations

import httpx
import pytest
import respx

from ark_mcp.domain.errors import ProviderError
from ark_mcp.domain.models import GenTaskStatus
from ark_mcp.providers.modelark.client import ModelArkGateway
from ark_mcp.providers.modelark.generation_tasks import (
    GenerationTaskService,
    task_timestamp,
    task_usage,
)
from ark_mcp.providers.modelark.schemas import GenerationTaskResponse, GenerationTaskUsage

MODELARK_BASE = "https://ark.ap-southeast.bytepluses.com/api/v3"


@pytest.fixture
def service() -> GenerationTaskService:
    gateway = ModelArkGateway(
        api_key="sk-test-key",  # pragma: allowlist secret
        base_url=MODELARK_BASE,
        timeout=10.0,
        connect_timeout=5.0,
    )
    return GenerationTaskService(gateway=gateway)


class TestSeedanceGetTask:
    """Tests for GET /contents/generations/tasks/{id}."""

    @respx.mock
    async def test_get_queued_task(self, service: GenerationTaskService) -> None:
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
    async def test_get_succeeded_task_with_video(self, service: GenerationTaskService) -> None:
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
    async def test_get_failed_task_with_error(self, service: GenerationTaskService) -> None:
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
    async def test_get_expired_task(self, service: GenerationTaskService) -> None:
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
    async def test_get_task_with_last_frame(self, service: GenerationTaskService) -> None:
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
    async def test_get_not_found(self, service: GenerationTaskService) -> None:
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
    async def test_list_success(self, service: GenerationTaskService) -> None:
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
    async def test_list_accepts_current_items_response(
        self, service: GenerationTaskService
    ) -> None:
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
    async def test_list_with_status_filter(self, service: GenerationTaskService) -> None:
        route = respx.get(f"{MODELARK_BASE}/contents/generations/tasks").mock(
            return_value=httpx.Response(
                200,
                json={"data": [], "total": 0},
            )
        )
        await service.list_tasks(status="succeeded")
        assert "filter.status=succeeded" in str(route.calls.last.request.url)

    @respx.mock
    async def test_list_with_task_id_filters(self, service: GenerationTaskService) -> None:
        route = respx.get(f"{MODELARK_BASE}/contents/generations/tasks").mock(
            return_value=httpx.Response(200, json={"data": [], "total": 0})
        )
        await service.list_tasks(task_ids=["task-1", "task-2"])
        request_url = str(route.calls.last.request.url)
        assert "filter.task_ids=task-1" in request_url
        assert "filter.task_ids=task-2" in request_url

    @respx.mock
    async def test_list_pagination_params(self, service: GenerationTaskService) -> None:
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
    async def test_delete_success(self, service: GenerationTaskService) -> None:
        respx.delete(f"{MODELARK_BASE}/contents/generations/tasks/task-1").mock(
            return_value=httpx.Response(
                204,
                headers={"X-Request-Id": "req-del"},
            )
        )
        request_id = await service.delete_task("task-1")
        assert request_id == "req-del"

    @respx.mock
    async def test_delete_not_found(self, service: GenerationTaskService) -> None:
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
    async def test_delete_timeout_ambiguous(self, service: GenerationTaskService) -> None:
        respx.delete(f"{MODELARK_BASE}/contents/generations/tasks/task-1").mock(
            side_effect=httpx.TimeoutException("timed out")
        )
        with pytest.raises(ProviderError) as exc_info:
            await service.delete_task("task-1")
        assert exc_info.value.code == "TIMEOUT"


class TestGenerationTaskInvalidResponse:
    """2xx responses with non-JSON bodies must fail as INVALID_RESPONSE."""

    @respx.mock
    async def test_get_empty_success_body(self, service: GenerationTaskService) -> None:
        respx.get(f"{MODELARK_BASE}/contents/generations/tasks/task-1").mock(
            return_value=httpx.Response(200, content=b"")
        )
        with pytest.raises(ProviderError) as exc_info:
            await service.get_task("task-1")
        assert exc_info.value.code == "INVALID_RESPONSE"

    @respx.mock
    async def test_list_non_json_success_body(self, service: GenerationTaskService) -> None:
        respx.get(f"{MODELARK_BASE}/contents/generations/tasks").mock(
            return_value=httpx.Response(200, content=b"not json")
        )
        with pytest.raises(ProviderError) as exc_info:
            await service.list_tasks()
        assert exc_info.value.code == "INVALID_RESPONSE"


class TestGenerationTaskHelpers:
    def test_usage_none_when_not_reported(self) -> None:
        assert task_usage(GenerationTaskResponse(id="t", status="queued")) is None

    def test_usage_carries_every_reported_counter(self) -> None:
        task = GenerationTaskResponse(
            id="t",
            status="succeeded",
            usage=GenerationTaskUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
        )
        usage = task_usage(task)
        assert usage is not None
        assert (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens) == (1, 2, 3)

    def test_epoch_timestamps_become_iso_8601(self) -> None:
        assert task_timestamp(1721400000) == "2024-07-19T14:40:00+00:00"
        assert task_timestamp("2026-10-17T00:00:00Z") == "2026-10-17T00:00:00Z"

    def test_file_url_reads_string_or_nested_url(self) -> None:
        flat = GenerationTaskResponse(id="t", content={"file_url": "https://x/model.glb"})
        nested = GenerationTaskResponse(id="t", content={"file_url": {"url": "https://x/m.zip"}})
        assert flat.file_url == "https://x/model.glb"
        assert nested.file_url == "https://x/m.zip"
        assert GenerationTaskResponse(id="t").file_url is None

    def test_unknown_provider_status_maps_to_unknown(self) -> None:
        assert GenTaskStatus("invented_status") is GenTaskStatus.UNKNOWN
