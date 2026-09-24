"""Integration tests for the ``ark_asset_*`` tools and ``asset://`` references.

The ModelArk OpenAPI host is replaced by an in-memory fake served through
respx, so the full path (input validation → signing → envelope parsing →
domain mapping → tool output) runs without network access.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx
from fastmcp.tools import ToolResult

from ark_mcp.providers.modelark.seedance import SeedanceService
from ark_mcp.providers.modelark.seedream import SeedreamService
from ark_mcp.tools._asset_models import (
    AssetCreateInput,
    AssetCreateOutput,
    AssetGroupEnsureInput,
    AssetGroupResult,
    AssetListInput,
    AssetPage,
    VerificationResultInput,
    VerificationResultOutput,
    VerificationStartInput,
    VerificationStartOutput,
)
from ark_mcp.tools.ark_asset_create import ark_asset_create
from ark_mcp.tools.ark_asset_group_ensure import ark_asset_group_ensure
from ark_mcp.tools.ark_asset_list import ark_asset_list
from ark_mcp.tools.ark_asset_verification_result import ark_asset_verification_result
from ark_mcp.tools.ark_asset_verification_start import ark_asset_verification_start
from ark_mcp.tools.seedance_create_task import SeedanceCreateTaskInput, seedance_create_task
from ark_mcp.tools.seedream_generate_image import SeedreamGenerateInput, seedream_generate_image
from tests.fixtures.fake_context import FakeContext

OPENAPI = "https://ark-openapi.test.example.com"


class FakeAssetApi:
    """Minimal in-memory stand-in for the asset library OpenAPI actions."""

    def __init__(self) -> None:
        self.groups: dict[str, dict[str, Any]] = {}
        self.assets: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.initial_status = "Active"
        self.fail_urls: set[str] = set()
        self.verification_group: str | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        action = request.url.params["Action"]
        body = json.loads(request.content or b"{}")
        self.calls.append((action, body))
        assert request.headers["Authorization"].startswith("HMAC-SHA256 ")
        handler = getattr(self, f"_{action}", None)
        if handler is None:
            return self._error(400, "InvalidAction", action)
        return handler(body)

    @staticmethod
    def _ok(result: dict[str, Any]) -> httpx.Response:
        return httpx.Response(200, json={"ResponseMetadata": {"RequestId": "r"}, "Result": result})

    @staticmethod
    def _error(status: int, code: str, message: str) -> httpx.Response:
        return httpx.Response(
            status,
            json={
                "ResponseMetadata": {"RequestId": "r", "Error": {"Code": code, "Message": message}}
            },
        )

    def _ListAssetGroups(self, body: dict[str, Any]) -> httpx.Response:
        if "Filter" not in body:
            return self._error(400, "MissingParameter.Filter", "Filter is required")
        wanted = body["Filter"].get("Name", "")
        items = [
            group
            for group in self.groups.values()
            if wanted in group["Name"] and group["GroupType"] == body["Filter"].get("GroupType")
        ]
        return self._ok({"Items": items})

    def _CreateAssetGroup(self, body: dict[str, Any]) -> httpx.Response:
        group_id = f"group-{len(self.groups) + 1}"
        self.groups[group_id] = {
            "Id": group_id,
            "Name": body["Name"],
            "GroupType": body.get("GroupType", "AIGC"),
            "ProjectName": body.get("ProjectName"),
        }
        return self._ok({"Id": group_id})

    def _CreateAsset(self, body: dict[str, Any]) -> httpx.Response:
        if body["URL"] in self.fail_urls:
            return self._error(400, "InvalidParameter.URL", "cannot fetch url")
        asset_id = f"asset-{len(self.assets) + 1}"
        self.assets[asset_id] = {
            "Id": asset_id,
            "GroupId": body["GroupId"],
            "AssetType": body["AssetType"],
            "Status": self.initial_status,
            "Name": body.get("Name", ""),
            "URL": f"https://ark-media-asset.example.com/{asset_id}.png?sig=1",
            "ProjectName": body.get("ProjectName"),
        }
        return self._ok({"Id": asset_id})

    def _GetAsset(self, body: dict[str, Any]) -> httpx.Response:
        asset = self.assets.get(body["Id"])
        if asset is None:
            return self._error(404, "NotFound.Asset", "asset not found")
        return self._ok(asset)

    def _ListAssets(self, body: dict[str, Any]) -> httpx.Response:
        if "Filter" not in body:
            return self._error(400, "MissingParameter.Filter", "Filter is required")
        return self._ok({"Items": list(self.assets.values()), "NextToken": "next-1"})

    def _CreateVisualValidateSession(self, body: dict[str, Any]) -> httpx.Response:
        return self._ok(
            {
                "BytedToken": "token-123456789",
                "H5Link": "https://h5.example.com/verify?x=1",
            }  # pragma: allowlist secret
        )

    def _GetVisualValidateResult(self, body: dict[str, Any]) -> httpx.Response:
        return self._ok({"GroupId": self.verification_group} if self.verification_group else {})


@pytest.fixture
def asset_api(test_env: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeAssetApi]:
    monkeypatch.setenv("BYTEPLUS_MODELARK_ASSET_CREATE_QPM", "6000")
    from ark_mcp.config.env import get_settings

    get_settings.cache_clear()
    api = FakeAssetApi()
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{OPENAPI}/").mock(side_effect=api.handle)
        yield api
    get_settings.cache_clear()


async def test_group_ensure_creates_then_reuses(
    asset_api: FakeAssetApi, fake_ctx: FakeContext
) -> None:
    first = await ark_asset_group_ensure(AssetGroupEnsureInput(subject="Teal Hero"), fake_ctx)
    second = await ark_asset_group_ensure(AssetGroupEnsureInput(subject="Teal Hero"), fake_ctx)

    assert isinstance(first, AssetGroupResult) and first.created is True
    assert isinstance(second, AssetGroupResult) and second.created is False
    assert second.group.group_id == first.group.group_id
    assert [action for action, _ in asset_api.calls].count("CreateAssetGroup") == 1


async def test_group_ensure_ignores_fuzzy_matches_and_rejects_duplicates(
    asset_api: FakeAssetApi, fake_ctx: FakeContext
) -> None:
    asset_api.groups["g-a"] = {"Id": "g-a", "Name": "Hero 2", "GroupType": "AIGC"}
    created = await ark_asset_group_ensure(AssetGroupEnsureInput(subject="Hero"), fake_ctx)
    assert isinstance(created, AssetGroupResult) and created.created is True

    asset_api.groups["g-b"] = {"Id": "g-b", "Name": "Hero", "GroupType": "AIGC"}
    with pytest.raises(ValueError, match="ambiguous_asset_group"):
        await ark_asset_group_ensure(AssetGroupEnsureInput(subject="Hero"), fake_ctx)


async def test_asset_create_by_subject_waits_for_active(
    asset_api: FakeAssetApi, fake_ctx: FakeContext
) -> None:
    result = await ark_asset_create(
        AssetCreateInput(
            subject="Teal Hero",
            sources=[
                {"url": "https://cdn.example.com/front.png", "name": "front"},
                {"url": "https://cdn.example.com/full.jpg"},
            ],
        ),
        fake_ctx,
    )

    assert isinstance(result, AssetCreateOutput)
    assert result.group_created is True
    assert result.all_active is True
    assert [item.asset_uri for item in result.items] == ["asset://asset-1", "asset://asset-2"]
    groups = {asset["GroupId"] for asset in asset_api.assets.values()}
    assert groups == {result.group_id}, "one subject's files must land in one group"
    create_bodies = [body for action, body in asset_api.calls if action == "CreateAsset"]
    assert all(body["AssetType"] == "Image" for body in create_bodies)
    assert all(body["ProjectName"] == "default" for body in create_bodies)


async def test_asset_create_reports_partial_failures(
    asset_api: FakeAssetApi, fake_ctx: FakeContext
) -> None:
    asset_api.fail_urls.add("https://cdn.example.com/broken.png")
    result = await ark_asset_create(
        AssetCreateInput(
            group_id="group-existing",
            wait_until_active=False,
            sources=[
                {"url": "https://cdn.example.com/ok.png"},
                {"url": "https://cdn.example.com/broken.png"},
            ],
        ),
        fake_ctx,
    )

    assert isinstance(result, AssetCreateOutput)
    assert result.items[0].asset_id == "asset-1"
    assert result.items[0].status == "Processing"
    assert result.items[1].asset_id is None
    assert "InvalidParameter.URL" in (result.items[1].error or "")
    assert result.all_active is False


async def test_asset_create_rejects_private_urls(
    asset_api: FakeAssetApi, fake_ctx: FakeContext
) -> None:
    with pytest.raises(ValueError):
        await ark_asset_create(
            AssetCreateInput(group_id="g-1", sources=[{"url": "https://127.0.0.1/a.png"}]),
            fake_ctx,
        )
    assert not asset_api.calls


async def test_asset_list_always_sends_filter(
    asset_api: FakeAssetApi, fake_ctx: FakeContext
) -> None:
    asset_api.assets["asset-9"] = {"Id": "asset-9", "Status": "Active", "AssetType": "Image"}
    page = await ark_asset_list(AssetListInput(), fake_ctx)

    assert isinstance(page, AssetPage)
    assert page.assets[0].asset_uri == "asset://asset-9"
    assert page.next_token == "next-1"
    list_body = next(body for action, body in asset_api.calls if action == "ListAssets")
    assert list_body["Filter"] == {"GroupType": "AIGC"}


async def test_verification_flow(asset_api: FakeAssetApi, fake_ctx: FakeContext) -> None:
    started = await ark_asset_verification_start(
        VerificationStartInput(callback_url="https://app.example.com/done"), fake_ctx
    )
    assert isinstance(started, VerificationStartOutput)
    assert started.h5_link.endswith("&lng=en")

    pending = await ark_asset_verification_result(
        VerificationResultInput(verification_token=started.verification_token), fake_ctx
    )
    assert isinstance(pending, VerificationResultOutput) and pending.status == "pending"

    asset_api.verification_group = "group-live-1"
    verified = await ark_asset_verification_result(
        VerificationResultInput(verification_token=started.verification_token), fake_ctx
    )
    assert isinstance(verified, VerificationResultOutput)
    assert verified.status == "verified"
    assert verified.group_id == "group-live-1"


async def test_verification_start_requires_https_callback(
    asset_api: FakeAssetApi, fake_ctx: FakeContext
) -> None:
    with pytest.raises(ValueError, match="callback_url"):
        await ark_asset_verification_start(VerificationStartInput(), fake_ctx)


# --- asset:// in generation tools --------------------------------------------


async def test_seedance_passes_asset_uri_after_preflight(
    asset_api: FakeAssetApi, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    asset_api.assets["asset-7"] = {"Id": "asset-7", "Status": "Active", "AssetType": "Image"}
    captured: dict[str, Any] = {}

    async def mock_create(self: SeedanceService, request: Any) -> tuple[str, str | None]:
        captured["content"] = [item.model_dump(exclude_none=True) for item in request.content]
        return "task-1", "req-1"

    async def mock_close(self: SeedanceService) -> None:
        return None

    monkeypatch.setattr(SeedanceService, "create_task", mock_create)
    monkeypatch.setattr(SeedanceService, "close", mock_close)

    result = await seedance_create_task(
        SeedanceCreateTaskInput(prompt="Image 1 waves", images=["asset://asset-7"]), fake_ctx
    )

    assert not isinstance(result, ToolResult)
    assert captured["content"][1]["image_url"] == {"url": "asset://asset-7"}
    assert ("GetAsset", {"Id": "asset-7", "ProjectName": "default"}) in asset_api.calls


async def test_seedance_preflight_blocks_processing_and_tolerates_unknown(
    asset_api: FakeAssetApi, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def mock_create(self: SeedanceService, request: Any) -> tuple[str, str | None]:
        return "task-2", "req-2"

    async def mock_close(self: SeedanceService) -> None:
        return None

    monkeypatch.setattr(SeedanceService, "create_task", mock_create)
    monkeypatch.setattr(SeedanceService, "close", mock_close)

    asset_api.assets["asset-busy"] = {"Id": "asset-busy", "Status": "Processing"}
    with pytest.raises(ValueError, match="asset_not_active"):
        await seedance_create_task(
            SeedanceCreateTaskInput(prompt="x", images=["asset://asset-busy"]), fake_ctx
        )

    # Not found (e.g. authorized by another account or copyright IP) must not block.
    result = await seedance_create_task(
        SeedanceCreateTaskInput(prompt="x", images=["asset://asset-shared"]), fake_ctx
    )
    assert not isinstance(result, ToolResult)


async def test_seedream_rejects_asset_uri_when_mode_off(
    asset_api: FakeAssetApi, fake_ctx: FakeContext
) -> None:
    with pytest.raises(ValueError, match="BYTEPLUS_MODELARK_ASSET_REFERENCE_MODE"):
        await seedream_generate_image(
            SeedreamGenerateInput(prompt="x", images=[{"kind": "url", "url": "asset://asset-1"}]),
            fake_ctx,
        )


async def test_seedream_resolve_mode_swaps_in_temporary_url(
    asset_api: FakeAssetApi, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BYTEPLUS_MODELARK_ASSET_REFERENCE_MODE", "resolve")
    from ark_mcp.config.env import get_settings

    get_settings.cache_clear()
    asset_api.assets["asset-3"] = {
        "Id": "asset-3",
        "Status": "Active",
        "AssetType": "Image",
        "URL": "https://ark-media-asset.example.com/asset-3.png?sig=abc",
    }
    captured: dict[str, Any] = {}

    async def mock_generate(self: SeedreamService, request: Any) -> Any:
        captured["image"] = request.image
        raise RuntimeError("stop after request build")

    monkeypatch.setattr(SeedreamService, "generate", mock_generate)
    with pytest.raises(RuntimeError, match="stop after request build"):
        await seedream_generate_image(
            SeedreamGenerateInput(
                prompt="x", images=[{"kind": "url", "url": "asset://asset-3"}], persist=False
            ),
            fake_ctx,
        )
    assert captured["image"] == "https://ark-media-asset.example.com/asset-3.png?sig=abc"


async def test_seedream_resolve_mode_checks_asset_type(
    asset_api: FakeAssetApi, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BYTEPLUS_MODELARK_ASSET_REFERENCE_MODE", "resolve")
    from ark_mcp.config.env import get_settings

    get_settings.cache_clear()
    asset_api.assets["asset-v"] = {
        "Id": "asset-v",
        "Status": "Active",
        "AssetType": "Video",
        "URL": "https://ark-media-asset.example.com/v.mp4",
    }
    with pytest.raises(ValueError, match="asset_type_mismatch"):
        await seedream_generate_image(
            SeedreamGenerateInput(prompt="x", images=[{"kind": "url", "url": "asset://asset-v"}]),
            fake_ctx,
        )
