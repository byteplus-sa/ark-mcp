"""seed_understand: strict JSON, json_retry, save_to, and return_content."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from ark_mcp.config.env import get_settings
from ark_mcp.domain.errors import NormalizedProviderError, ProviderError
from ark_mcp.providers.modelark.schemas import ChatCompletionProviderResponse
from ark_mcp.providers.modelark.understanding import SeedUnderstandingService
from ark_mcp.security.output_paths import OutputPathError
from ark_mcp.tools.seed_understand import (
    TOOL_ANNOTATIONS,
    SeedUnderstandInput,
    SeedUnderstandOutput,
    UnderstandingResponseFormat,
    seed_understand,
)
from tests.fixtures.fake_context import FakeContext

BEATS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["beats"],
    "properties": {
        "beats": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["start"],
                "properties": {"start": {"type": "number"}},
            },
        }
    },
}


def _response(content: str, *, finish_reason: str = "stop", reasoning: str = "") -> dict[str, Any]:
    return {
        "id": "chatcmpl-json",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                    "reasoning_content": reasoning or None,
                },
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "total_tokens": 30,
            "completion_tokens_details": {"reasoning_tokens": 15},
        },
    }


def _patch(monkeypatch: pytest.MonkeyPatch, responses: list[dict[str, Any]]) -> list[Any]:
    captured: list[Any] = []

    async def mock_generate(
        self: SeedUnderstandingService, request: Any
    ) -> tuple[ChatCompletionProviderResponse, str | None]:
        captured.append(request)
        body = responses[min(len(captured) - 1, len(responses) - 1)]
        return ChatCompletionProviderResponse.model_validate(body), "req-json"

    monkeypatch.setattr(SeedUnderstandingService, "generate", mock_generate)
    return captured


def _schema_format() -> dict[str, Any]:
    return {"type": "json_schema", "json_schema": {"name": "beats", "schema": BEATS_SCHEMA}}


@pytest.fixture
def output_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setenv("OUTPUT_ROOTS", str(root))
    get_settings.cache_clear()
    return root.resolve()


class TestStrictJson:
    async def test_schema_is_sent_under_schema_alias(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _patch(monkeypatch, [_response('{"beats": [{"start": 1.5}]}')])
        result = await seed_understand(
            SeedUnderstandInput(prompt="beats", response_format=_schema_format()), fake_ctx
        )
        assert isinstance(result, SeedUnderstandOutput)
        payload = captured[0].model_dump(exclude_none=True)["response_format"]
        assert payload["json_schema"]["schema"] == BEATS_SCHEMA
        assert "schema_" not in payload["json_schema"]
        assert result.choices[0].parsed == {"beats": [{"start": 1.5}]}
        assert result.choices[0].schema_violation is None
        assert result.usage.reasoning_tokens == 15

    async def test_violation_is_reported_not_raised(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch(monkeypatch, [_response('{"beats": [{"start": "soon"}]}')])
        result = await seed_understand(
            SeedUnderstandInput(prompt="beats", response_format=_schema_format()), fake_ctx
        )
        assert isinstance(result, SeedUnderstandOutput)
        violation = result.choices[0].schema_violation
        assert violation is not None
        assert violation.path == "/beats/0/start"
        assert result.choices[0].parsed is None
        assert result.choices[0].content == '{"beats": [{"start": "soon"}]}'

    async def test_json_retry_recovers(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _patch(
            monkeypatch,
            [_response('{"beats": [{"start": "x"}]}'), _response('{"beats": [{"start": 2}]}')],
        )
        result = await seed_understand(
            SeedUnderstandInput(prompt="beats", response_format=_schema_format(), json_retry=1),
            fake_ctx,
        )
        assert isinstance(result, SeedUnderstandOutput)
        assert result.attempts == 2
        assert result.choices[0].parsed == {"beats": [{"start": 2}]}
        assert result.usage.total_tokens == 60
        assert "previous answer was rejected" in captured[1].messages[-1].content[-1].text

    async def test_json_retry_skipped_when_truncated(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _patch(monkeypatch, [_response('{"beats": [', finish_reason="length")])
        result = await seed_understand(
            SeedUnderstandInput(prompt="beats", response_format=_schema_format(), json_retry=2),
            fake_ctx,
        )
        assert isinstance(result, SeedUnderstandOutput)
        assert len(captured) == 1
        violation = result.choices[0].schema_violation
        assert violation is not None
        assert violation.finish_reason == "length"

    def test_invalid_schema_rejected_before_billing(self) -> None:
        with pytest.raises(ValidationError, match="invalid"):
            UnderstandingResponseFormat.model_validate(
                {"type": "json_schema", "json_schema": {"name": "x", "schema": {"type": 5}}}
            )

    def test_json_schema_requires_schema(self) -> None:
        with pytest.raises(ValidationError, match="required"):
            UnderstandingResponseFormat.model_validate({"type": "json_schema"})


class TestOutputControls:
    async def test_return_content_summary_truncates(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch(monkeypatch, [_response("x" * 5000)])
        result = await seed_understand(
            SeedUnderstandInput(prompt="long", return_content="summary"), fake_ctx
        )
        assert isinstance(result, SeedUnderstandOutput)
        assert len(result.choices[0].content) == 2000
        assert result.choices[0].content_chars == 5000
        assert result.choices[0].content_truncated is True

    async def test_save_to_writes_parsed_json_without_reasoning(
        self,
        test_env: None,
        output_root: Path,
        fake_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch(
            monkeypatch,
            [_response('{"beats": [{"start": 1}]}', reasoning="secret chain of thought")],
        )
        target = output_root / "elements" / "e1" / "beats.json"
        result = await seed_understand(
            SeedUnderstandInput(
                prompt="beats",
                response_format=_schema_format(),
                save_to=str(target),
                return_content="none",
            ),
            fake_ctx,
        )
        assert isinstance(result, SeedUnderstandOutput)
        assert result.saved_path == str(target)
        assert json.loads(target.read_text()) == {"beats": [{"start": 1}]}
        assert "secret" not in target.read_text()
        assert result.choices[0].content == ""
        assert result.choices[0].parsed == {"beats": [{"start": 1}]}

    async def test_save_to_outside_root_rejected_before_billing(
        self,
        test_env: None,
        output_root: Path,
        fake_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        captured = _patch(monkeypatch, [_response("ok")])
        with pytest.raises(OutputPathError, match="inside an allowed output root"):
            await seed_understand(
                SeedUnderstandInput(prompt="x", save_to=str(tmp_path / "elsewhere.txt")),
                fake_ctx,
            )
        assert captured == []

    async def test_save_to_disabled_without_roots(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OUTPUT_ROOTS", "")
        get_settings.cache_clear()
        _patch(monkeypatch, [_response("ok")])
        with pytest.raises(OutputPathError, match="disabled"):
            await seed_understand(
                SeedUnderstandInput(prompt="x", save_to="/tmp/anything.txt"), fake_ctx
            )

    def test_tool_is_not_read_only(self) -> None:
        assert TOOL_ANNOTATIONS["readOnlyHint"] is False


async def test_retry_failure_keeps_the_billed_first_answer(
    test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A json_retry attempt that fails must not discard the billed first answer."""
    calls = 0

    async def mock_generate(
        self: SeedUnderstandingService, request: Any
    ) -> tuple[ChatCompletionProviderResponse, str | None]:
        nonlocal calls
        calls += 1
        if calls == 1:
            body = _response('{"beats": [{"start": "x"}]}')
            return ChatCompletionProviderResponse.model_validate(body), "req-json"
        raise ProviderError(
            NormalizedProviderError(
                provider="modelark",
                operation="chat_completion",
                code="TIMEOUT",
                message="timed out",
                retryable=True,
                ambiguous_completion=False,
            )
        )

    monkeypatch.setattr(SeedUnderstandingService, "generate", mock_generate)
    result = await seed_understand(
        SeedUnderstandInput(prompt="beats", response_format=_schema_format(), json_retry=1),
        fake_ctx,
    )
    assert isinstance(result, SeedUnderstandOutput)
    assert calls == 2
    assert result.attempts == 1
    assert result.choices[0].content == '{"beats": [{"start": "x"}]}'
    assert result.choices[0].schema_violation is not None
    assert result.usage.total_tokens == 30
