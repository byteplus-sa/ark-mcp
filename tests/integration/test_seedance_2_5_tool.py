"""Integration tests for the Seedance 2.5 tool handlers.

Exercises create and variations through the full tool path with mocked
provider responses and a temp artifact store.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ark_mcp.providers.modelark.generation_tasks import GenerationTaskService
from ark_mcp.providers.modelark.schemas import GenerationTaskResponse
from ark_mcp.providers.modelark.seedance import SeedanceService
from ark_mcp.tools.seedance_create_task import (
    SeedanceAudioInput,
    SeedanceCreateTaskInput,
    SeedanceCreateTaskOutput,
    SeedanceImageInput,
    SeedanceVideoInput,
    seedance_create_task,
)
from tests.fixtures.fake_context import FakeContext


async def _mock_close(self: SeedanceService) -> None:
    pass


@pytest.fixture
def seedance_2_5_env(test_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure environment with both Seedance 2.0 and 2.5 model bindings."""
    monkeypatch.setenv(
        "SEEDANCE_MODEL_BINDINGS",
        '[{"model_id":"dreamina-seedance-2-0-260128","family":"standard"},'
        '{"model_id":"dreamina-seedance-2-5-260628","family":"seedance_2_5"}]',
    )
    monkeypatch.setenv("SEEDANCE_DEFAULT_MODEL", "dreamina-seedance-2-5-260628")
    monkeypatch.setenv("SEEDANCE_MODEL_FAMILY", "seedance_2_5")

    from ark_mcp.config.env import get_settings
    from ark_mcp.config.model_capabilities import refresh_capability_registry

    get_settings.cache_clear()
    refresh_capability_registry()

    yield

    get_settings.cache_clear()
    refresh_capability_registry()


@pytest.fixture
async def seedance_2_5_ctx(seedance_2_5_env: None) -> FakeContext:
    from ark_mcp.config.env import get_settings
    from ark_mcp.runtime import close_runtime_services, create_runtime_services
    from tests.fixtures.fake_context import FakeContext

    runtime = await create_runtime_services(get_settings())
    try:
        yield FakeContext(lifespan_context={"runtime": runtime})
    finally:
        await close_runtime_services(runtime)


class TestSeedance25CreateTaskTool:
    """Integration tests for seedance_create_task."""

    async def test_create_task_success(
        self,
        seedance_2_5_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def mock_create(self: SeedanceService, request: Any) -> tuple[str, str | None]:
            assert request.model == "dreamina-seedance-2-5-260628"
            return "task-2-5-abc", "req-2-5"

        monkeypatch.setattr(SeedanceService, "create_task", mock_create)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        result = await seedance_create_task(
            SeedanceCreateTaskInput(
                prompt="a cinematic 30-second film",
                duration=25,
                resolution="720p",
            ),
            seedance_2_5_ctx,
        )

        assert isinstance(result, SeedanceCreateTaskOutput)
        assert result.tasks[0].task_id == "task-2-5-abc"
        assert result.submitted == 1

    async def test_create_task_1080p_passthrough(
        self,
        seedance_2_5_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def mock_create(self: SeedanceService, request: Any) -> tuple[str, str | None]:
            assert request.model == "dreamina-seedance-2-5-260628"
            assert request.resolution == "1080p"
            return "task-1080p", "req-1080p"

        monkeypatch.setattr(SeedanceService, "create_task", mock_create)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        result = await seedance_create_task(
            SeedanceCreateTaskInput(
                prompt="a cinematic 30-second film",
                resolution="1080p",
            ),
            seedance_2_5_ctx,
        )

        assert isinstance(result, SeedanceCreateTaskOutput)
        assert result.tasks[0].task_id == "task-1080p"
        assert result.submitted == 1

    async def test_create_task_with_max_references(
        self,
        seedance_2_5_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def mock_create(self: SeedanceService, request: Any) -> tuple[str, str | None]:
            return "task-refs", "req-refs"

        monkeypatch.setattr(SeedanceService, "create_task", mock_create)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        images = [
            SeedanceImageInput(kind="url", url=f"https://example.com/img{i}.jpg") for i in range(15)
        ]
        videos = [SeedanceVideoInput(url=f"https://example.com/vid{i}.mp4") for i in range(5)]

        result = await seedance_create_task(
            SeedanceCreateTaskInput(
                prompt="multi-reference generation",
                images=images,
                videos=videos,
                duration=20,
            ),
            seedance_2_5_ctx,
        )

        assert isinstance(result, SeedanceCreateTaskOutput)
        assert result.tasks[0].task_id == "task-refs"

    async def test_create_task_audio_only(
        self,
        seedance_2_5_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Seedance 2.5 supports audio-only input (unique to 2.5)."""

        async def mock_create(self: SeedanceService, request: Any) -> tuple[str, str | None]:
            return "task-audio", "req-audio"

        monkeypatch.setattr(SeedanceService, "create_task", mock_create)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        result = await seedance_create_task(
            SeedanceCreateTaskInput(
                prompt="visualize the music",
                audios=[SeedanceAudioInput(kind="url", url="https://example.com/song.mp3")],
                duration=10,
            ),
            seedance_2_5_ctx,
        )

        assert isinstance(result, SeedanceCreateTaskOutput)
        assert result.tasks[0].task_id == "task-audio"

    async def test_unconfigured_model_id_suggests_the_nearest(
        self,
        test_env: None,
        fake_ctx: FakeContext,
    ) -> None:
        with pytest.raises(ValueError) as excinfo:
            await seedance_create_task(
                SeedanceCreateTaskInput(prompt="test", model="dreamina-seedance-2.0"),
                fake_ctx,
            )
        message = str(excinfo.value)
        assert "is not configured" in message
        assert "Did you mean 'dreamina-seedance-2-0-260128'?" in message

    async def test_2_0_model_rejects_2_5_only_options_at_runtime(
        self,
        seedance_2_5_ctx: FakeContext,
    ) -> None:
        with pytest.raises(ValueError) as excinfo:
            await seedance_create_task(
                SeedanceCreateTaskInput(
                    prompt="test",
                    model="dreamina-seedance-2-0-260128",
                    duration=20,
                    output_format="mov",
                    audios=["https://example.com/a.mp3"],
                ),
                seedance_2_5_ctx,
            )
        message = str(excinfo.value)
        assert "duration 20s is outside [4, 15]" in message
        assert "output_format 'mov' is not supported" in message
        assert "audio cannot be the only media input" in message

    async def test_create_task_passes_omni_reference_task_type(
        self,
        seedance_2_5_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured_request: list[Any] = []

        async def mock_create(self: SeedanceService, request: Any) -> tuple[str, str | None]:
            captured_request.append(request)
            return "task-edit-2-5", "req-edit-2-5"

        monkeypatch.setattr(SeedanceService, "create_task", mock_create)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        result = await seedance_create_task(
            SeedanceCreateTaskInput(
                prompt="extend the video with a sunset scene",
                videos=[SeedanceVideoInput(url="https://example.com/source.mp4")],
                omni_reference_task_type="extend",
            ),
            seedance_2_5_ctx,
        )

        assert isinstance(result, SeedanceCreateTaskOutput)
        assert captured_request[0].omni_reference_task_type == "extend"


class TestSeedance25CreateTaskVariationsTool:
    """Integration tests for seedance_create_task."""

    async def test_variations_success(
        self,
        seedance_2_5_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        call_count = 0

        async def mock_create(self: SeedanceService, request: Any) -> tuple[str, str | None]:
            nonlocal call_count
            call_count += 1
            assert request.model == "dreamina-seedance-2-5-260628"
            return f"task-var-{call_count}", f"req-var-{call_count}"

        monkeypatch.setattr(SeedanceService, "create_task", mock_create)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        result = await seedance_create_task(
            SeedanceCreateTaskInput(
                variations=3,
                variation_prompts=["prompt a", "prompt b", "prompt c"],
                duration=20,
            ),
            seedance_2_5_ctx,
        )

        assert isinstance(result, SeedanceCreateTaskOutput)
        assert len(result.tasks) == 3
        assert result.submitted == 3
        assert result.failed == 0

    async def test_dry_run_returns_redacted_requests_without_billing(
        self,
        seedance_2_5_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def fail_create(self: SeedanceService, request: object) -> tuple[str, str | None]:
            raise AssertionError("dry_run must not submit")

        monkeypatch.setattr(SeedanceService, "create_task", fail_create)
        result = await seedance_create_task(
            SeedanceCreateTaskInput(
                variations=2,
                variation_prompts=["a fox", "a cat"],
                images=["https://cdn.example.com/private/frame.png?sig=secret"],
                dry_run=True,
            ),
            seedance_2_5_ctx,
        )
        assert isinstance(result, SeedanceCreateTaskOutput)
        assert result.dry_run is True
        assert result.tasks == []
        assert result.preview is not None
        assert len(result.preview.requests) == 2
        assert result.preview.estimated_cost_usd == 0.7
        image = result.preview.requests[0]["content"][1]
        assert image["image_url"]["url"] == "https://cdn.example.com"
        assert await get_runtime_owned(seedance_2_5_ctx) == set()

    async def test_partial_variation_failures_are_reported(
        self,
        seedance_2_5_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from ark_mcp.domain.errors import NormalizedProviderError, ProviderError

        calls: list[object] = []

        async def flaky_create(self: SeedanceService, request: object) -> tuple[str, str | None]:
            calls.append(request)
            if len(calls) == 1:
                return "cgt-ok", "req-ok"
            raise ProviderError(
                NormalizedProviderError(
                    provider="modelark",
                    operation="create_task",
                    code="InvalidParameter",
                    message="bad prompt",
                    retryable=False,
                )
            )

        monkeypatch.setattr(SeedanceService, "create_task", flaky_create)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)
        result = await seedance_create_task(
            SeedanceCreateTaskInput(variations=2, variation_prompts=["ok", "bad"]),
            seedance_2_5_ctx,
        )
        assert isinstance(result, SeedanceCreateTaskOutput)
        assert (result.submitted, result.failed) == (1, 1)
        failed = [task for task in result.tasks if task.error is not None]
        assert failed[0].task_id is None
        assert failed[0].error is not None and failed[0].error.code == "InvalidParameter"


def _draft_task(
    *,
    status: str = "succeeded",
    draft: bool | None = True,
    model: str = "dreamina-seedance-2-5-260628",
    age: timedelta = timedelta(hours=1),
    created_at: int | str | object | None = ...,
) -> GenerationTaskResponse:
    if created_at is ...:
        created_at = int((datetime.now(UTC) - age).timestamp())
    return GenerationTaskResponse(
        id="cgt-draft",
        model=model,
        status=status,
        draft=draft,
        created_at=created_at,  # type: ignore[arg-type]
    )


class TestSeedance25DraftMode:
    """Draft mode: 480p preview, then a final video rendered from the Draft task."""

    async def test_draft_task_sends_draft_flag_at_480p(
        self, seedance_2_5_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: list[Any] = []

        async def mock_create(self: SeedanceService, request: Any) -> tuple[str, str | None]:
            captured.append(request)
            return "cgt-draft", "req-draft"

        monkeypatch.setattr(SeedanceService, "create_task", mock_create)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        result = await seedance_create_task(
            SeedanceCreateTaskInput(prompt="a girl holds a fox", draft=True, duration=5),
            seedance_2_5_ctx,
        )

        assert isinstance(result, SeedanceCreateTaskOutput)
        body = captured[0].model_dump(exclude_none=True)
        assert body["draft"] is True
        assert body["resolution"] == "480p"
        assert body["content"][0] == {"type": "text", "text": "a girl holds a fox"}

    async def test_final_from_draft_sends_only_draft_reference(
        self, seedance_2_5_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: list[Any] = []

        async def mock_get(self: SeedanceService, task_id: str) -> tuple[Any, str | None]:
            assert task_id == "cgt-draft"
            return _draft_task(), "req-get"

        async def mock_create(self: SeedanceService, request: Any) -> tuple[str, str | None]:
            captured.append(request)
            return "cgt-final", "req-final"

        monkeypatch.setattr(GenerationTaskService, "get_task", mock_get)

        monkeypatch.setattr(GenerationTaskService, "close", _mock_close)
        monkeypatch.setattr(SeedanceService, "create_task", mock_create)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        result = await seedance_create_task(
            SeedanceCreateTaskInput(draft_task_id="cgt-draft", return_last_frame=True),
            seedance_2_5_ctx,
        )

        assert isinstance(result, SeedanceCreateTaskOutput)
        assert result.tasks[0].task_id == "cgt-final"
        body = captured[0].model_dump(exclude_none=True)
        assert body == {
            "model": "dreamina-seedance-2-5-260628",
            "content": [{"type": "draft_task", "draft_task": {"id": "cgt-draft"}}],
            "resolution": "1080p",
            "watermark": False,
            "return_last_frame": True,
        }

    @pytest.mark.parametrize("status", ["queued", "running", "failed"])
    async def test_final_from_unfinished_draft_rejected(
        self, seedance_2_5_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch, status: str
    ) -> None:
        async def mock_get(self: SeedanceService, task_id: str) -> tuple[Any, str | None]:
            return _draft_task(status=status), None

        monkeypatch.setattr(GenerationTaskService, "get_task", mock_get)

        monkeypatch.setattr(GenerationTaskService, "close", _mock_close)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        with pytest.raises(ValueError, match=f"is {status}; only a succeeded Draft"):
            await seedance_create_task(
                SeedanceCreateTaskInput(draft_task_id="cgt-draft"), seedance_2_5_ctx
            )

    async def test_final_from_non_draft_task_rejected(
        self, seedance_2_5_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def mock_get(self: SeedanceService, task_id: str) -> tuple[Any, str | None]:
            return _draft_task(draft=False), None

        monkeypatch.setattr(GenerationTaskService, "get_task", mock_get)

        monkeypatch.setattr(GenerationTaskService, "close", _mock_close)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        with pytest.raises(ValueError, match="not created in Draft mode"):
            await seedance_create_task(
                SeedanceCreateTaskInput(draft_task_id="cgt-draft"), seedance_2_5_ctx
            )

    async def test_final_from_expired_draft_rejected(
        self, seedance_2_5_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def mock_get(self: SeedanceService, task_id: str) -> tuple[Any, str | None]:
            return _draft_task(age=timedelta(days=8)), None

        monkeypatch.setattr(GenerationTaskService, "get_task", mock_get)

        monkeypatch.setattr(GenerationTaskService, "close", _mock_close)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        with pytest.raises(ValueError, match="more than 7 days ago"):
            await seedance_create_task(
                SeedanceCreateTaskInput(draft_task_id="cgt-draft"), seedance_2_5_ctx
            )

    async def test_final_from_expired_iso_created_at_rejected(
        self, seedance_2_5_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        old = (datetime.now(UTC) - timedelta(days=8)).isoformat()

        async def mock_get(self: SeedanceService, task_id: str) -> tuple[Any, str | None]:
            return _draft_task(created_at=old), None

        monkeypatch.setattr(GenerationTaskService, "get_task", mock_get)

        monkeypatch.setattr(GenerationTaskService, "close", _mock_close)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        with pytest.raises(ValueError, match="more than 7 days ago"):
            await seedance_create_task(
                SeedanceCreateTaskInput(draft_task_id="cgt-draft"), seedance_2_5_ctx
            )

    @pytest.mark.parametrize(
        "created_at",
        [
            lambda: (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            lambda: (datetime.now(UTC) - timedelta(hours=1)).replace(tzinfo=None).isoformat(),
            lambda: None,
            lambda: "not-a-timestamp",
        ],
        ids=["fresh-iso", "naive-iso", "missing", "unparsable"],
    )
    async def test_final_skips_age_check_when_created_at_is_fresh_or_unknown(
        self,
        seedance_2_5_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
        created_at: Any,
    ) -> None:
        async def mock_get(self: SeedanceService, task_id: str) -> tuple[Any, str | None]:
            return _draft_task(created_at=created_at()), None

        async def mock_create(self: SeedanceService, request: Any) -> tuple[str, str | None]:
            return "cgt-final", None

        monkeypatch.setattr(GenerationTaskService, "get_task", mock_get)

        monkeypatch.setattr(GenerationTaskService, "close", _mock_close)
        monkeypatch.setattr(SeedanceService, "create_task", mock_create)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        result = await seedance_create_task(
            SeedanceCreateTaskInput(draft_task_id="cgt-draft"), seedance_2_5_ctx
        )
        assert isinstance(result, SeedanceCreateTaskOutput)

    async def test_final_with_mismatched_model_rejected(
        self, seedance_2_5_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def mock_get(self: SeedanceService, task_id: str) -> tuple[Any, str | None]:
            return _draft_task(model="dreamina-seedance-2-5-other"), None

        monkeypatch.setattr(GenerationTaskService, "get_task", mock_get)

        monkeypatch.setattr(GenerationTaskService, "close", _mock_close)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        with pytest.raises(ValueError, match="does not match the Draft task's model"):
            await seedance_create_task(
                SeedanceCreateTaskInput(
                    draft_task_id="cgt-draft", model="dreamina-seedance-2-5-260628"
                ),
                seedance_2_5_ctx,
            )

    async def test_final_from_draft_owned_by_another_principal_rejected(
        self, seedance_2_5_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from ark_mcp.runtime import get_runtime
        from ark_mcp.security.auth_context import PrincipalContext

        await get_runtime(seedance_2_5_ctx).ownership_store.record(
            "modelark", "cgt-draft", PrincipalContext(principal_id="someone-else")
        )

        async def mock_get(self: SeedanceService, task_id: str) -> tuple[Any, str | None]:
            raise AssertionError("provider must not be called for a foreign Draft task")

        monkeypatch.setattr(GenerationTaskService, "get_task", mock_get)

        monkeypatch.setattr(GenerationTaskService, "close", _mock_close)

        with pytest.raises(PermissionError):
            await seedance_create_task(
                SeedanceCreateTaskInput(draft_task_id="cgt-draft"), seedance_2_5_ctx
            )

    async def test_draft_variations_send_draft_flag(
        self, seedance_2_5_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: list[Any] = []

        async def mock_create(self: SeedanceService, request: Any) -> tuple[str, str | None]:
            captured.append(request)
            return f"cgt-draft-{len(captured)}", None

        monkeypatch.setattr(SeedanceService, "create_task", mock_create)
        monkeypatch.setattr(SeedanceService, "close", _mock_close)

        result = await seedance_create_task(
            SeedanceCreateTaskInput(variations=2, prompt="a fox", draft=True),
            seedance_2_5_ctx,
        )

        assert result.submitted == 2
        assert all(r.draft is True and r.resolution == "480p" for r in captured)


async def get_runtime_owned(ctx: FakeContext) -> set[str]:
    from tests.fixtures.ownership import owned_task_ids

    return await owned_task_ids(ctx.lifespan_context["runtime"], "modelark")
