"""Unit tests for the normalized provider error model."""

from __future__ import annotations

import pytest

from ark_mcp.domain.errors import NormalizedProviderError, ProviderError


class TestNormalizedProviderError:
    """Tests for NormalizedProviderError model."""

    def test_all_fields_set(self) -> None:
        error = NormalizedProviderError(
            provider="modelark",
            operation="generate_image",
            http_status=403,
            code="FORBIDDEN",
            message="model not activated",
            request_id="req-abc-123",
            retryable=False,
            ambiguous_completion=False,
        )
        assert error.provider == "modelark"
        assert error.operation == "generate_image"
        assert error.http_status == 403
        assert error.code == "FORBIDDEN"
        assert error.message == "model not activated"
        assert error.request_id == "req-abc-123"
        assert error.retryable is False
        assert error.ambiguous_completion is False

    def test_defaults_for_optional_fields(self) -> None:
        error = NormalizedProviderError(
            provider="seed-speech",
            operation="generate_audio",
            message="audio generation failed",
            retryable=True,
        )
        assert error.http_status is None
        assert error.code is None
        assert error.request_id is None
        assert error.ambiguous_completion is None

    def test_seed_speech_provider(self) -> None:
        error = NormalizedProviderError(
            provider="seed-speech",
            operation="generate",
            message="failed",
            retryable=False,
        )
        assert error.provider == "seed-speech"

    def test_modelark_provider(self) -> None:
        error = NormalizedProviderError(
            provider="modelark",
            operation="create_task",
            message="failed",
            retryable=False,
        )
        assert error.provider == "modelark"

    def test_empty_message(self) -> None:
        error = NormalizedProviderError(
            provider="modelark",
            operation="op",
            message="",
            retryable=False,
        )
        assert error.message == ""

    def test_none_request_id(self) -> None:
        error = NormalizedProviderError(
            provider="modelark",
            operation="op",
            message="failed",
            request_id=None,
            retryable=False,
        )
        assert error.request_id is None

    def test_http_status_none(self) -> None:
        error = NormalizedProviderError(
            provider="modelark",
            operation="op",
            http_status=None,
            message="timeout",
            retryable=False,
        )
        assert error.http_status is None

    def test_ambiguous_completion_true(self) -> None:
        error = NormalizedProviderError(
            provider="modelark",
            operation="create_task",
            message="timed out",
            retryable=False,
            ambiguous_completion=True,
        )
        assert error.ambiguous_completion is True

    def test_retryable_true(self) -> None:
        error = NormalizedProviderError(
            provider="modelark",
            operation="create_task",
            http_status=429,
            message="rate limited",
            retryable=True,
        )
        assert error.retryable is True

    def test_serialization(self) -> None:
        error = NormalizedProviderError(
            provider="modelark",
            operation="generate_image",
            http_status=500,
            code="INTERNAL",
            message="server error",
            request_id="req-1",
            retryable=True,
            ambiguous_completion=None,
        )
        data = error.model_dump()
        assert data["provider"] == "modelark"
        assert data["operation"] == "generate_image"
        assert data["http_status"] == 500
        assert data["code"] == "INTERNAL"
        assert data["message"] == "server error"
        assert data["request_id"] == "req-1"
        assert data["retryable"] is True
        assert data["ambiguous_completion"] is None


class TestProviderError:
    """Tests for ProviderError exception."""

    def _make_normalized(self) -> NormalizedProviderError:
        return NormalizedProviderError(
            provider="modelark",
            operation="generate_image",
            http_status=403,
            code="FORBIDDEN",
            message="model not activated",
            request_id="req-abc-123",
            retryable=False,
            ambiguous_completion=False,
        )

    def test_wraps_normalized_error(self) -> None:
        normalized = self._make_normalized()
        exc = ProviderError(normalized)
        assert exc.error is normalized

    def test_normalized_property(self) -> None:
        normalized = self._make_normalized()
        exc = ProviderError(normalized)
        assert exc.normalized is normalized

    def test_provider_property(self) -> None:
        exc = ProviderError(self._make_normalized())
        assert exc.provider == "modelark"

    def test_operation_property(self) -> None:
        exc = ProviderError(self._make_normalized())
        assert exc.operation == "generate_image"

    def test_http_status_property(self) -> None:
        exc = ProviderError(self._make_normalized())
        assert exc.http_status == 403

    def test_code_property(self) -> None:
        exc = ProviderError(self._make_normalized())
        assert exc.code == "FORBIDDEN"

    def test_message_property(self) -> None:
        exc = ProviderError(self._make_normalized())
        assert exc.message == "model not activated"

    def test_request_id_property(self) -> None:
        exc = ProviderError(self._make_normalized())
        assert exc.request_id == "req-abc-123"

    def test_retryable_property(self) -> None:
        exc = ProviderError(self._make_normalized())
        assert exc.retryable is False

    def test_ambiguous_completion_property(self) -> None:
        exc = ProviderError(self._make_normalized())
        assert exc.ambiguous_completion is False

    def test_str_representation(self) -> None:
        exc = ProviderError(self._make_normalized())
        assert str(exc) == "model not activated"

    def test_is_exception(self) -> None:
        exc = ProviderError(self._make_normalized())
        assert isinstance(exc, Exception)

    def test_can_be_raised(self) -> None:
        with pytest.raises(ProviderError) as exc_info:
            raise ProviderError(self._make_normalized())
        assert exc_info.value.message == "model not activated"

    def test_can_be_caught_as_exception(self) -> None:
        with pytest.raises(Exception, match="model not activated"):
            raise ProviderError(self._make_normalized())

    def test_none_request_id(self) -> None:
        normalized = NormalizedProviderError(
            provider="modelark",
            operation="op",
            message="failed",
            request_id=None,
            retryable=False,
        )
        exc = ProviderError(normalized)
        assert exc.request_id is None

    def test_none_http_status(self) -> None:
        normalized = NormalizedProviderError(
            provider="modelark",
            operation="op",
            http_status=None,
            message="timeout",
            retryable=False,
        )
        exc = ProviderError(normalized)
        assert exc.http_status is None

    def test_empty_message(self) -> None:
        normalized = NormalizedProviderError(
            provider="modelark",
            operation="op",
            message="",
            retryable=False,
        )
        exc = ProviderError(normalized)
        assert exc.message == ""
        assert str(exc) == ""

    def test_none_ambiguous_completion(self) -> None:
        normalized = NormalizedProviderError(
            provider="modelark",
            operation="op",
            message="timed out",
            retryable=False,
            ambiguous_completion=None,
        )
        exc = ProviderError(normalized)
        assert exc.ambiguous_completion is None

    def test_none_code(self) -> None:
        normalized = NormalizedProviderError(
            provider="modelark",
            operation="op",
            code=None,
            message="failed",
            retryable=False,
        )
        exc = ProviderError(normalized)
        assert exc.code is None

    def test_seed_speech_provider_error(self) -> None:
        normalized = NormalizedProviderError(
            provider="seed-speech",
            operation="generate",
            http_status=401,
            code="UNAUTHORIZED",
            message="invalid API key",
            request_id="log-123",
            retryable=False,
        )
        exc = ProviderError(normalized)
        assert exc.provider == "seed-speech"
        assert exc.http_status == 401

    def test_delegate_properties_match_wrapped(self) -> None:
        normalized = self._make_normalized()
        exc = ProviderError(normalized)
        assert exc.provider == normalized.provider
        assert exc.operation == normalized.operation
        assert exc.http_status == normalized.http_status
        assert exc.code == normalized.code
        assert exc.message == normalized.message
        assert exc.request_id == normalized.request_id
        assert exc.retryable == normalized.retryable
        assert exc.ambiguous_completion == normalized.ambiguous_completion
