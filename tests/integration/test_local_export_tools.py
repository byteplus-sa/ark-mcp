"""output_path / output_dir on generation tools, and persistence fallbacks end to end."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from ark_mcp.artifacts.store import ArtifactPersistenceError
from ark_mcp.config.env import get_settings
from ark_mcp.providers.modelark.schemas import SeedreamProviderResponse
from ark_mcp.providers.modelark.seedream import SeedreamService
from ark_mcp.security.output_paths import OutputPathError
from ark_mcp.security.safe_downloader import DownloadedMedia
from ark_mcp.tools.seedream_generate_image import (
    SeedreamGenerateInput,
    SeedreamGenerateOutput,
    seedream_generate_image,
)
from tests.fixtures.fake_context import FakeContext

PNG = b"\x89PNG-fake"


def _patch(monkeypatch: pytest.MonkeyPatch, data: list[dict[str, Any]]) -> list[Any]:
    calls: list[Any] = []

    async def mock_generate(
        self: SeedreamService, request: Any
    ) -> tuple[SeedreamProviderResponse, str | None]:
        calls.append(request)
        return SeedreamProviderResponse.model_validate({"created": 1, "data": data}), "req"

    monkeypatch.setattr(SeedreamService, "generate", mock_generate)
    return calls


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "projects"
    path.mkdir()
    monkeypatch.setenv("OUTPUT_ROOTS", str(path))
    get_settings.cache_clear()
    return path.resolve()


async def test_output_path_writes_file(
    test_env: None, root: Path, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch(monkeypatch, [{"b64_json": base64.b64encode(PNG).decode(), "output_format": "png"}])
    target = root / "elements" / "e7" / "hero.png"
    result = await seedream_generate_image(
        SeedreamGenerateInput(prompt="hero", output_path=str(target)), fake_ctx
    )
    assert isinstance(result, SeedreamGenerateOutput)
    assert result.artifacts[0].local_path == str(target)
    assert target.read_bytes() == PNG
    assert result.artifacts[0].uri.startswith("seed-media://")


async def test_output_dir_names_files_by_artifact_id(
    test_env: None, root: Path, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch(monkeypatch, [{"b64_json": base64.b64encode(PNG).decode(), "output_format": "png"}])
    result = await seedream_generate_image(
        SeedreamGenerateInput(prompt="hero", output_path=str(root / "e8") + "/"), fake_ctx
    )
    assert isinstance(result, SeedreamGenerateOutput)
    ref = result.artifacts[0]
    assert ref.local_path == str(root / "e8" / f"{ref.id}.png")


async def test_bad_output_path_rejected_before_provider_call(
    test_env: None,
    root: Path,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = _patch(monkeypatch, [{"b64_json": base64.b64encode(PNG).decode()}])
    with pytest.raises(OutputPathError):
        await seedream_generate_image(
            SeedreamGenerateInput(prompt="x", output_path=str(tmp_path / "outside.png")),
            fake_ctx,
        )
    assert calls == []


async def test_multi_image_requires_directory(
    test_env: None, root: Path, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patch(monkeypatch, [])
    with pytest.raises(OutputPathError, match="directory"):
        await seedream_generate_image(
            SeedreamGenerateInput(prompt="x", max_images=3, output_path=str(root / "a.png")),
            fake_ctx,
        )
    assert calls == []


async def test_persistence_failure_returns_provider_url_and_keeps_other_items(
    test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch(
        monkeypatch,
        [
            {"b64_json": base64.b64encode(PNG).decode(), "output_format": "png"},
            {"url": "https://tos-ap-southeast.bytepluses.com/second.png", "output_format": "png"},
        ],
    )
    with patch(
        "ark_mcp.artifacts.filesystem_store.FilesystemArtifactStore.copy_from_trusted_url",
        new=AsyncMock(
            side_effect=ArtifactPersistenceError(
                "download_failed", "Provider output download timed out.", retryable=True
            )
        ),
    ):
        result = await seedream_generate_image(SeedreamGenerateInput(prompt="two"), fake_ctx)
    assert isinstance(result, SeedreamGenerateOutput)
    stored, fallback = result.artifacts
    assert stored.uri.startswith("seed-media://")
    assert stored.persistence_error is None
    assert fallback.id == "provider-url"
    assert fallback.uri == "https://tos-ap-southeast.bytepluses.com/second.png"
    assert fallback.persistence_error is not None
    assert fallback.persistence_error.code == "download_failed"


async def test_download_retry_then_success(
    test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch(monkeypatch, [{"url": "https://tos-ap-southeast.bytepluses.com/x.png"}])
    download = AsyncMock(
        return_value=DownloadedMedia(
            body=PNG,
            content_type="image/png",
            final_url="https://tos-ap-southeast.bytepluses.com/x.png",
        )
    )
    with patch("ark_mcp.security.safe_downloader.SafeDownloader._download_once", new=download):
        result = await seedream_generate_image(SeedreamGenerateInput(prompt="x"), fake_ctx)
    assert isinstance(result, SeedreamGenerateOutput)
    assert result.artifacts[0].persistence_error is None
