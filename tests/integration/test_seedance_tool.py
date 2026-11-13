"""Integration tests for the Seedance tool handlers.

Exercises task creation through the full tool path with mocked provider
responses. Polling, listing and cancellation are covered in
``test_gen_task_tools.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp.tools import ToolResult

from ark_mcp.domain.errors import NormalizedProviderError, ProviderError
from ark_mcp.providers.modelark.seedance import SeedanceService
from ark_mcp.tools.seedance_create_task import (
    SeedanceAudioInput,
    SeedanceCreateTaskInput,
    SeedanceCreateTaskOutput,
    SeedanceVideoInput,
    seedance_create_task,
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
        assert result.tasks[0].task_id == "task-abc-123"
        assert result.submitted == 1
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
        assert result.tasks[0].task_id == "task-text-001"
        assert result.submitted == 1
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
        assert result.tasks[0].task_id == "task-mix-001"
        assert result.submitted == 1

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
