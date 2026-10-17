"""Shared fixtures for contract tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from ark_mcp.config.env import get_settings


@pytest.fixture
def all_services_enabled(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name, value in {
        "BYTEPLUS_MODELARK_API_KEY": "sk-test",  # pragma: allowlist secret
        "BYTEPLUS_SEED_SPEECH_API_KEY": "sk-test",  # pragma: allowlist secret
        "BYTEPLUS_VOD_MEDIAKIT_API_KEY": "test-mediakit-key",  # pragma: allowlist secret
        "BYTEPLUS_CV_API_KEY": "test-cv-key",  # pragma: allowlist secret
        "BYTEPLUS_OMNIHUMAN_ENABLED": "true",
        "BYTEPLUS_MODELARK_3D_ENABLED": "true",
        "BYTEPLUS_MODELARK_SEEDANCE_2_5_PREMIUM_ENABLED": "true",
        "BYTEPLUS_MODELARK_ACCESS_KEY": "AKLTtestopenapi",
        "BYTEPLUS_MODELARK_SECRET_KEY": "test-openapi-secret",  # pragma: allowlist secret
        "BYTEPLUS_MODELARK_ASSETS_ALLOW_DELETE": "true",
        "TOS_ACCESS_KEY": "ak-test-tos",  # pragma: allowlist secret
        "TOS_SECRET_KEY": "sk-test-tos",  # pragma: allowlist secret
        "TOS_BUCKET": "test-bucket",
    }.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
