"""Integration tests for the ``seedance_create_task`` tool."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from ark_mcp.domain.errors import NormalizedProviderError, ProviderError
from ark_mcp.providers.modelark.seedance import SeedanceService
from ark_mcp.tools.seedance_create_task import (
    SeedanceCreateTaskInput,
    SeedanceCreateTaskOutput,
    SeedanceVideoInput,
    seedance_create_task,
)
from tests.fixtures.fake_context import FakeContext


def _patch_seedance_by_prompt(
    monkeypatch: pytest.MonkeyPatch,
    responses_by_prompt: dict[str, dict[str, str] | Exception],
) -> None:
    """Mock SeedanceService.create_task to return different results based on the prompt."""

    async def mock_create(self: SeedanceService, request: Any) -> tuple[str, str | None]:
        prompt = ""
        for item in request.content:
            if item.type == "text" and item.text:
                prompt = item.text
                break
        response = responses_by_prompt.get(prompt)
        if isinstance(response, Exception):
            raise response
        return response["task_id"], response.get("request_id")

    monkeypatch.setattr(SeedanceService, "create_task", mock_create)

    async def mock_close(self: SeedanceService) -> None:
        pass

    monkeypatch.setattr(SeedanceService, "close", mock_close)


class TestSeedanceVariationsTool:
    """Integration tests for seedance_create_task."""

    @pytest.mark.parametrize("base_prompt", [None, "base prompt"])
    @pytest.mark.parametrize("variations", [1, 2])
    async def test_prompt_overrides_match_preview_and_submission(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
        base_prompt: str | None,
        variations: int,
    ) -> None:
        create = AsyncMock(return_value=("task-override", "req-override"))
        monkeypatch.setattr(SeedanceService, "create_task", create)
        monkeypatch.setattr(SeedanceService, "close", AsyncMock())
        prompts = [f"override {index}" for index in range(variations)]
        request = SeedanceCreateTaskInput(
            prompt=base_prompt,
            variation_prompts=prompts,
            variations=variations,
            dry_run=True,
        )

        preview = await seedance_create_task(request, fake_ctx)

        assert isinstance(preview, SeedanceCreateTaskOutput)
        assert preview.preview is not None
        assert [body["content"] for body in preview.preview.requests] == [
            [{"type": "text", "text": prompt}] for prompt in prompts
        ]
        create.assert_not_awaited()

        result = await seedance_create_task(request.model_copy(update={"dry_run": False}), fake_ctx)

        assert isinstance(result, SeedanceCreateTaskOutput)
        assert result.submitted == variations
        assert result.failed == 0
        assert [
            call.args[0].model_dump(exclude_none=True) for call in create.await_args_list
        ] == preview.preview.requests

    async def test_three_tasks_all_succeed(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_seedance_by_prompt(
            monkeypatch,
            {
                "a cat": {"task_id": "task-1", "request_id": "req-1"},
                "a dog": {"task_id": "task-2", "request_id": "req-2"},
                "a bird": {"task_id": "task-3", "request_id": "req-3"},
            },
        )

        result = await seedance_create_task(
            SeedanceCreateTaskInput(
                variation_prompts=["a cat", "a dog", "a bird"],
                variations=3,
                videos=[SeedanceVideoInput(url="https://example.com/v.mp4")],
            ),
            fake_ctx,
        )

        assert isinstance(result, SeedanceCreateTaskOutput)
        assert len(result.tasks) == 3
        assert result.submitted == 3
        assert result.failed == 0
        assert result.tasks[0].task_id == "task-1"
        assert result.tasks[1].task_id == "task-2"
        assert result.tasks[2].task_id == "task-3"
        assert result.recommended_poll_after_ms == 5000

    async def test_partial_failure(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_seedance_by_prompt(
            monkeypatch,
            {
                "good": {"task_id": "task-1"},
                "bad": ProviderError(
                    NormalizedProviderError(
                        provider="modelark",
                        operation="create_task",
                        http_status=400,
                        code="INVALID_PARAM",
                        message="bad",
                        retryable=False,
                    )
                ),
            },
        )

        result = await seedance_create_task(
            SeedanceCreateTaskInput(
                variation_prompts=["good", "bad", "good"],
                variations=3,
                videos=[SeedanceVideoInput(url="https://example.com/v.mp4")],
            ),
            fake_ctx,
        )

        assert result.submitted == 2
        assert result.failed == 1
        assert result.tasks[1].error is not None
        assert result.tasks[1].task_id is None

    async def test_no_prompt_raises(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="Either prompt or variation_prompts"):
            SeedanceCreateTaskInput(
                variations=2,
                videos=[SeedanceVideoInput(url="https://example.com/v.mp4")],
            )

    async def test_text_only_variations_succeeds(self) -> None:
        inp = SeedanceCreateTaskInput(prompt="test", variations=1)
        assert inp.prompt == "test"
        assert inp.images is None
        assert inp.videos is None
        assert inp.audios is None

    async def test_variation_prompts_only_no_media_valid(self) -> None:
        inp = SeedanceCreateTaskInput(variation_prompts=["a cat playing"], variations=1)
        assert inp.prompt is None
        assert inp.variation_prompts == ["a cat playing"]
        assert inp.images is None
        assert inp.videos is None
        assert inp.audios is None

    async def test_too_many_variations_raises(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            SeedanceCreateTaskInput(
                prompt="test",
                variations=6,
                videos=[SeedanceVideoInput(url="https://example.com/v.mp4")],
            )

    async def test_per_model_reference_limit_is_checked_at_runtime(
        self, test_env: None, fake_ctx: FakeContext
    ) -> None:
        videos = [SeedanceVideoInput(url=f"https://example.com/v{i}.mp4") for i in range(4)]
        with pytest.raises(ValueError, match="4 reference videos exceed the maximum of 3"):
            await seedance_create_task(
                SeedanceCreateTaskInput(prompt="test", variations=2, videos=videos), fake_ctx
            )
