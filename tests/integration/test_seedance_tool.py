"""Integration tests for the Seedance tool handlers.

Exercises create, get, list, and cancel/delete through the full tool path
with mocked provider responses and a temp artifact store.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastmcp.tools import ToolResult

from ark_mcp.domain.errors import NormalizedProviderError, ProviderError
from ark_mcp.providers.modelark.schemas import (
    SeedanceGenerationUsage,
    SeedanceTaskResponse,
)
from ark_mcp.providers.modelark.seedance import SeedanceService
from ark_mcp.security.safe_downloader import DownloadedMedia
from ark_mcp.tools.seedance_cancel_or_delete_task import (
    SeedanceCancelOrDeleteInput,
    SeedanceCancelOrDeleteOutput,
    seedance_cancel_or_delete_task,
)
from ark_mcp.tools.seedance_create_task import (
    SeedanceAudioInput,
    SeedanceCreateTaskInput,
    SeedanceCreateTaskOutput,
    SeedanceVideoInput,
    seedance_create_task,
)
from ark_mcp.tools.seedance_get_task import (
    SeedanceGetTaskInput,
    SeedanceTaskOutput,
    seedance_get_task,
)
from ark_mcp.tools.seedance_list_tasks import (
    SeedanceListTasksInput,
    SeedanceTaskPage,
    seedance_list_tasks,
)
from tests.fixtures.fake_context import FakeContext


async def _mock_close(self: SeedanceService) -> None:
    pass


class TestSeedanceCreateTaskTool:
    """Integration tests for seedance_create_task."""

    async def test_create_task_success(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def mock_create(self: SeedanceService, request: Any) -> tuple[str, str | None]:
            return "task-abc-123", "req-789"

        monkeypatch.setattr(SeedanceService, "create_task", mock_create)

        async def mock_close(self: SeedanceService) -> None:
            pass

        monkeypatch.setattr(SeedanceService, "close", mock_close)

        result = await seedance_create_task(
            SeedanceCreateTaskInput(
                prompt="a cat walking",
                videos=[SeedanceVideoInput(url="https://example.com/cat.mp4")],
            ),
            fake_ctx,
        )

        assert isinstance(result, SeedanceCreateTaskOutput)
        assert result.task_id == "task-abc-123"
        assert result.status == "queued"
        assert result.recommended_poll_after_ms == 5000

    async def test_text_only_create_task_succeeds(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured_content: list[Any] = []

        async def mock_create(self: SeedanceService, request: Any) -> tuple[str, str | None]:
            captured_content.extend(request.content)
            return "task-text-001", "req-text"

        monkeypatch.setattr(SeedanceService, "create_task", mock_create)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        result = await seedance_create_task(
            SeedanceCreateTaskInput(prompt="just text"),
            fake_ctx,
        )

        assert isinstance(result, SeedanceCreateTaskOutput)
        assert result.task_id == "task-text-001"
        assert result.status == "queued"
        assert result.recommended_poll_after_ms == 5000

        assert len(captured_content) == 1
        assert captured_content[0].type == "text"
        assert captured_content[0].text == "just text"

    async def test_create_task_prompt_with_video_and_audio_succeeds(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured_content: list[Any] = []

        async def mock_create(self: SeedanceService, request: Any) -> tuple[str, str | None]:
            captured_content.extend(request.content)
            return "task-mix-001", "req-mix"

        monkeypatch.setattr(SeedanceService, "create_task", mock_create)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        result = await seedance_create_task(
            SeedanceCreateTaskInput(
                prompt="A dancer moving to music",
                videos=[SeedanceVideoInput(url="https://example.com/dance.mp4")],
                audios=[SeedanceAudioInput(kind="url", url="https://example.com/song.wav")],
            ),
            fake_ctx,
        )

        assert isinstance(result, SeedanceCreateTaskOutput)
        assert result.task_id == "task-mix-001"
        assert result.status == "queued"

        content_types = [item.type for item in captured_content]
        assert "text" in content_types
        assert "video_url" in content_types
        assert "audio_url" in content_types

    async def test_provider_error_propagates(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def mock_create(self: SeedanceService, request: Any) -> tuple[str, str | None]:
            raise ProviderError(
                NormalizedProviderError(
                    provider="modelark",
                    operation="create_task",
                    http_status=400,
                    code="INVALID_PARAM",
                    message="content is empty",
                    retryable=False,
                )
            )

        monkeypatch.setattr(SeedanceService, "create_task", mock_create)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        result = await seedance_create_task(
            SeedanceCreateTaskInput(
                videos=[SeedanceVideoInput(url="https://example.com/v.mp4")],
            ),
            fake_ctx,
        )
        assert isinstance(result, ToolResult)
        assert result.is_error
        assert result.structured_content is None
        assert "http_status=400" in result.content[0].text


class TestSeedanceGetTaskTool:
    """Integration tests for seedance_get_task."""

    async def test_get_queued_task(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        task = SeedanceTaskResponse(
            id="task-1",
            model="dreamina-seedance-2-0-260128",
            status="queued",
            created_at=1721400000,
            updated_at=1721400000,
        )

        async def mock_get(
            self: SeedanceService, task_id: str
        ) -> tuple[SeedanceTaskResponse, str | None]:
            return task, "req-get-1"

        monkeypatch.setattr(SeedanceService, "get_task", mock_get)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        result = await seedance_get_task(SeedanceGetTaskInput(task_id="task-1"), fake_ctx)

        assert isinstance(result, SeedanceTaskOutput)
        assert result.task_id == "task-1"
        assert result.status == "queued"
        assert result.video is None
        assert result.last_frame is None

    async def test_get_succeeded_task_persists_video(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        temp_store: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await fake_ctx.lifespan_context["runtime"].task_artifact_cache.clear()

        task = SeedanceTaskResponse(
            id="task-succ",
            model="dreamina-seedance-2-0-260128",
            status="succeeded",
            created_at=1721400000,
            updated_at=1721400100,
            content={"video_url": "https://tos-ap-southeast.bytepluses.com/video.mp4"},
            usage=SeedanceGenerationUsage(completion_tokens=100, prompt_tokens=10),
        )

        async def mock_get(
            self: SeedanceService, task_id: str
        ) -> tuple[SeedanceTaskResponse, str | None]:
            return task, "req-get-2"

        monkeypatch.setattr(SeedanceService, "get_task", mock_get)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        with (
            patch("ark_mcp.artifacts.registry.get_artifact_store", return_value=temp_store),
            patch(
                "ark_mcp.security.safe_downloader.SafeDownloader.download",
                new=AsyncMock(
                    return_value=DownloadedMedia(
                        body=b"fake-video-bytes",
                        content_type="video/mp4",
                        final_url="https://tos-ap-southeast.bytepluses.com/video.mp4",
                    )
                ),
            ),
        ):
            result = await seedance_get_task(SeedanceGetTaskInput(task_id="task-succ"), fake_ctx)

        assert result.status == "succeeded"
        assert result.video is not None
        assert result.video.uri.startswith("seed-media://artifacts/")
        assert result.video.media_type == "video"
        assert result.usage is not None
        assert result.usage.completion_tokens == 100

        # Verify stored.
        stored = await temp_store.get(result.video.id)
        assert stored.data == b"fake-video-bytes"

    async def test_persistence_cache_prevents_double_download(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        temp_store: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        task_artifact_cache = fake_ctx.lifespan_context["runtime"].task_artifact_cache
        await task_artifact_cache.clear()

        # Pre-populate cache to simulate a previous get call.
        from datetime import UTC, datetime

        from ark_mcp.domain.artifacts import ArtifactRef

        cached_ref = ArtifactRef(
            id="cached-video-id",
            uri="seed-media://artifacts/cached-video-id",
            media_type="video",
            mime_type="video/mp4",
            bytes=100,
            sha256="abc123",
            created_at=datetime.now(UTC).isoformat(),
        )
        await task_artifact_cache.set(
            "modelark", "task-cached", {"video": cached_ref, "last_frame": None}
        )

        task = SeedanceTaskResponse(
            id="task-cached",
            model="dreamina-seedance-2-0-260128",
            status="succeeded",
            created_at=1721400000,
            updated_at=1721400100,
            content={"video_url": "https://tos-ap-southeast.bytepluses.com/video.mp4"},
        )

        async def mock_get(
            self: SeedanceService, task_id: str
        ) -> tuple[SeedanceTaskResponse, str | None]:
            return task, "req-cached"

        monkeypatch.setattr(SeedanceService, "get_task", mock_get)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        with patch("ark_mcp.artifacts.registry.get_artifact_store", return_value=temp_store):
            result = await seedance_get_task(SeedanceGetTaskInput(task_id="task-cached"), fake_ctx)

        # Should return the cached ref, not download again.
        assert result.video is not None
        assert result.video.id == "cached-video-id"

        await fake_ctx.lifespan_context["runtime"].task_artifact_cache.clear()

    async def test_get_failed_task_with_error(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from ark_mcp.providers.modelark.schemas import SeedanceErrorDetail

        task = SeedanceTaskResponse(
            id="task-fail",
            model="dreamina-seedance-2-0-260128",
            status="failed",
            created_at=1721400000,
            updated_at=1721400050,
            error=SeedanceErrorDetail(code="MODERATION_FAILED", message="content rejected"),
        )

        async def mock_get(
            self: SeedanceService, task_id: str
        ) -> tuple[SeedanceTaskResponse, str | None]:
            return task, "req-fail"

        monkeypatch.setattr(SeedanceService, "get_task", mock_get)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        result = await seedance_get_task(SeedanceGetTaskInput(task_id="task-fail"), fake_ctx)

        assert result.status == "failed"
        assert result.error is not None
        assert result.error.code == "MODERATION_FAILED"


class TestSeedanceListTasksTool:
    """Integration tests for seedance_list_tasks."""

    async def test_list_success(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from ark_mcp.providers.modelark.schemas import (
            SeedanceTaskListResponse,
        )

        page_response = SeedanceTaskListResponse(
            data=[
                SeedanceTaskResponse(
                    id="t1",
                    model="dreamina-seedance-2-0-260128",
                    status="succeeded",
                    created_at=1721400000,
                    updated_at=1721400100,
                ),
                SeedanceTaskResponse(
                    id="t2",
                    model="dreamina-seedance-2-0-260128",
                    status="running",
                    created_at=1721400200,
                    updated_at=1721400300,
                ),
            ],
            total=2,
            has_more=False,
        )

        async def mock_list(self: SeedanceService, **kwargs: Any) -> tuple[Any, str | None]:
            return page_response, "req-list"

        monkeypatch.setattr(SeedanceService, "list_tasks", mock_list)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        result = await seedance_list_tasks(SeedanceListTasksInput(page=1, page_size=20), fake_ctx)

        assert isinstance(result, SeedanceTaskPage)
        assert len(result.tasks) == 2
        assert result.total == 2
        assert result.page == 1
        assert result.page_size == 20


class TestSeedanceCancelOrDeleteTool:
    """Integration tests for seedance_cancel_or_delete_task."""

    async def test_delete_succeeded_task(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        task = SeedanceTaskResponse(
            id="task-del",
            model="dreamina-seedance-2-0-260128",
            status="succeeded",
            created_at=1721400000,
            updated_at=1721400100,
        )

        async def mock_get(
            self: SeedanceService, task_id: str
        ) -> tuple[SeedanceTaskResponse, str | None]:
            return task, "req-del-get"

        async def mock_delete(self: SeedanceService, task_id: str) -> str | None:
            return "req-del-del"

        monkeypatch.setattr(SeedanceService, "get_task", mock_get)
        monkeypatch.setattr(SeedanceService, "delete_task", mock_delete)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        result = await seedance_cancel_or_delete_task(
            SeedanceCancelOrDeleteInput(
                task_id="task-del",
                mode="delete",
                expected_status="succeeded",
            ),
            fake_ctx,
        )

        assert isinstance(result, SeedanceCancelOrDeleteOutput)
        assert result.mode == "delete"
        assert result.previous_status == "succeeded"

    async def test_cancel_queued_task(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        task = SeedanceTaskResponse(
            id="task-cancel",
            model="dreamina-seedance-2-0-260128",
            status="queued",
            created_at=1721400000,
            updated_at=1721400000,
        )

        async def mock_get(
            self: SeedanceService, task_id: str
        ) -> tuple[SeedanceTaskResponse, str | None]:
            return task, "req-cancel-get"

        async def mock_delete(self: SeedanceService, task_id: str) -> str | None:
            return "req-cancel-del"

        monkeypatch.setattr(SeedanceService, "get_task", mock_get)
        monkeypatch.setattr(SeedanceService, "delete_task", mock_delete)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        result = await seedance_cancel_or_delete_task(
            SeedanceCancelOrDeleteInput(
                task_id="task-cancel",
                mode="cancel",
                expected_status="queued",
            ),
            fake_ctx,
        )

        assert result.mode == "cancel"
        assert result.previous_status == "queued"

    async def test_status_mismatch_rejected(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        task = SeedanceTaskResponse(
            id="task-mismatch",
            model="dreamina-seedance-2-0-260128",
            status="running",
            created_at=1721400000,
            updated_at=1721400000,
        )

        async def mock_get(
            self: SeedanceService, task_id: str
        ) -> tuple[SeedanceTaskResponse, str | None]:
            return task, "req-mm"

        monkeypatch.setattr(SeedanceService, "get_task", mock_get)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        with pytest.raises(ValueError, match=r"has status 'running'.*expected 'queued'"):
            await seedance_cancel_or_delete_task(
                SeedanceCancelOrDeleteInput(
                    task_id="task-mismatch",
                    mode="cancel",
                    expected_status="queued",
                ),
                fake_ctx,
            )
