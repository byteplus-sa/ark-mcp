"""Integration tests for the ``ark_asset_*`` tools and ``asset://`` references.

The ModelArk OpenAPI host is replaced by an in-memory fake served through
respx, so the full path (input validation → signing → envelope parsing →
domain mapping → tool output) runs without network access.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx
from fastmcp.tools import ToolResult
from pydantic import ValidationError

from ark_mcp.providers.modelark.assets import AssetService
from ark_mcp.providers.modelark.seedance import SeedanceService
from ark_mcp.providers.modelark.seedream import SeedreamService
from ark_mcp.tools._asset_models import (
    AssetCreateInput,
    AssetCreateOutput,
    AssetDeleteInput,
    AssetDeleteResult,
    AssetGroupEnsureInput,
    AssetGroupListInput,
    AssetGroupPage,
    AssetGroupResult,
    AssetListInput,
    AssetPage,
    AssetUpdateInput,
    AssetUpdateResult,
    VerificationResultInput,
    VerificationResultOutput,
    VerificationStartInput,
    VerificationStartOutput,
)
from ark_mcp.tools._asset_shared import preflight_seedance_assets
from ark_mcp.tools.ark_asset_create import ark_asset_create
from ark_mcp.tools.ark_asset_delete import ark_asset_delete
from ark_mcp.tools.ark_asset_group_ensure import ark_asset_group_ensure
from ark_mcp.tools.ark_asset_group_list import ark_asset_group_list
from ark_mcp.tools.ark_asset_list import ark_asset_list
from ark_mcp.tools.ark_asset_update import ark_asset_update
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
        self.ambiguous_urls: set[str] = set()
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

    def _GetAssetGroup(self, body: dict[str, Any]) -> httpx.Response:
        group = self.groups.get(body["Id"])
        if group is None:
            return self._error(404, "NotFound.AssetGroup", "group not found")
        return self._ok(group)

    def _UpdateAssetGroup(self, body: dict[str, Any]) -> httpx.Response:
        group = self.groups.get(body["Id"])
        if group is None:
            return self._error(404, "NotFound.AssetGroup", "group not found")
        group.update({key: body[key] for key in ("Name", "Description") if key in body})
        return self._ok({})

    def _DeleteAssetGroup(self, body: dict[str, Any]) -> httpx.Response:
        group_id = body["Id"]
        if self.groups.pop(group_id, None) is None:
            return self._error(404, "NotFound.AssetGroup", "group not found")
        self.assets = {
            asset_id: asset
            for asset_id, asset in self.assets.items()
            if asset["GroupId"] != group_id
        }
        return self._ok({})

    def _CreateAsset(self, body: dict[str, Any]) -> httpx.Response:
        if body["URL"] in self.ambiguous_urls:
            return self._error(503, "InternalError", "response lost")
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

    def _UpdateAsset(self, body: dict[str, Any]) -> httpx.Response:
        asset = self.assets.get(body["Id"])
        if asset is None:
            return self._error(404, "NotFound.Asset", "asset not found")
        asset["Name"] = body["Name"]
        return self._ok({})

    def _DeleteAsset(self, body: dict[str, Any]) -> httpx.Response:
        if self.assets.pop(body["Id"], None) is None:
            return self._error(404, "NotFound.Asset", "asset not found")
        return self._ok({})

    def _ListAssets(self, body: dict[str, Any]) -> httpx.Response:
        if "Filter" not in body:
            return self._error(400, "MissingParameter.Filter", "Filter is required")
        group_ids = body["Filter"].get("GroupIds")
        items = [
            asset
            for asset in self.assets.values()
            if group_ids is None or asset.get("GroupId") in group_ids
        ]
        return self._ok({"Items": items[: body.get("MaxResults", 100)], "NextToken": "next-1"})

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


async def test_concurrent_group_ensure_reuses_one_group(
    asset_api: FakeAssetApi, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    original_create = AssetService.create_group

    async def delayed_create(service: AssetService, **kwargs: Any) -> str:
        started.set()
        await release.wait()
        return await original_create(service, **kwargs)

    monkeypatch.setattr(AssetService, "create_group", delayed_create)
    first = asyncio.create_task(
        ark_asset_group_ensure(AssetGroupEnsureInput(subject="Teal Hero"), fake_ctx)
    )
    await started.wait()
    second = asyncio.create_task(
        ark_asset_group_ensure(AssetGroupEnsureInput(subject="Teal Hero"), fake_ctx)
    )
    await asyncio.sleep(0.05)
    release.set()
    first_result, second_result = await asyncio.gather(first, second)

    assert isinstance(first_result, AssetGroupResult)
    assert isinstance(second_result, AssetGroupResult)
    assert first_result.group.group_id == second_result.group.group_id
    assert [action for action, _ in asset_api.calls].count("CreateAssetGroup") == 1


async def test_group_ensure_fails_closed_when_scan_is_incomplete(
    asset_api: FakeAssetApi, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def endless_pages(service: AssetService, **kwargs: Any) -> tuple[list[Any], str]:
        return [], "next-page"

    monkeypatch.setattr(AssetService, "list_groups", endless_pages)
    with pytest.raises(ValueError, match="asset_group_scan_incomplete"):
        await ark_asset_group_ensure(AssetGroupEnsureInput(subject="Teal Hero"), fake_ctx)
    assert not any(action == "CreateAssetGroup" for action, _ in asset_api.calls)


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


async def test_asset_create_reports_ambiguous_item_failure(
    asset_api: FakeAssetApi, fake_ctx: FakeContext
) -> None:
    asset_api.ambiguous_urls.add("https://cdn.example.com/uncertain.png")
    result = await ark_asset_create(
        AssetCreateInput(
            group_id="group-existing",
            wait_until_active=False,
            sources=[{"url": "https://cdn.example.com/uncertain.png"}],
        ),
        fake_ctx,
    )

    assert isinstance(result, AssetCreateOutput)
    assert result.items[0].asset_id is None
    assert "ambiguous_completion=True" in (result.items[0].error or "")
    assert "retryable=False" in (result.items[0].error or "")
    assert "inspect" in (result.items[0].error or "")


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


async def test_asset_delete_removes_only_requested_asset(
    asset_api: FakeAssetApi, fake_ctx: FakeContext
) -> None:
    asset_api.assets["asset-1"] = {"Id": "asset-1", "GroupId": "group-1"}
    asset_api.assets["asset-2"] = {"Id": "asset-2", "GroupId": "group-1"}

    result = await ark_asset_delete(
        AssetDeleteInput(asset_id="asset://asset-1", confirm=True), fake_ctx
    )

    assert isinstance(result, AssetDeleteResult)
    assert (result.id, result.kind, result.deleted) == ("asset-1", "asset", True)
    assert set(asset_api.assets) == {"asset-2"}
    assert asset_api.calls == [("DeleteAsset", {"Id": "asset-1", "ProjectName": "default"})]


async def test_group_delete_refuses_non_empty_group_without_cascade(
    asset_api: FakeAssetApi, fake_ctx: FakeContext
) -> None:
    asset_api.groups["group-1"] = {"Id": "group-1", "Name": "Test", "GroupType": "AIGC"}
    asset_api.assets["asset-1"] = {"Id": "asset-1", "GroupId": "group-1"}

    with pytest.raises(ValueError, match="asset_group_not_empty"):
        await ark_asset_delete(AssetDeleteInput(group_id="group-1", confirm=True), fake_ctx)

    assert set(asset_api.groups) == {"group-1"}
    assert set(asset_api.assets) == {"asset-1"}
    assert not any(action == "DeleteAssetGroup" for action, _ in asset_api.calls)


async def test_group_delete_with_cascade_removes_group_and_its_assets(
    asset_api: FakeAssetApi, fake_ctx: FakeContext
) -> None:
    asset_api.groups["group-1"] = {"Id": "group-1", "Name": "Test", "GroupType": "AIGC"}
    asset_api.groups["group-2"] = {"Id": "group-2", "Name": "Other", "GroupType": "AIGC"}
    asset_api.assets["asset-1"] = {"Id": "asset-1", "GroupId": "group-1"}
    asset_api.assets["asset-2"] = {"Id": "asset-2", "GroupId": "group-2"}

    result = await ark_asset_delete(
        AssetDeleteInput(group_id="group-1", confirm=True, cascade=True), fake_ctx
    )

    assert isinstance(result, AssetDeleteResult)
    assert (result.id, result.kind, result.deleted) == ("group-1", "group", True)
    assert set(asset_api.groups) == {"group-2"}
    assert set(asset_api.assets) == {"asset-2"}
    assert asset_api.calls == [("DeleteAssetGroup", {"Id": "group-1", "ProjectName": "default"})]


async def test_group_delete_checks_assets_of_its_own_group_type(
    asset_api: FakeAssetApi, fake_ctx: FakeContext
) -> None:
    asset_api.groups["live-1"] = {"Id": "live-1", "Name": "Person", "GroupType": "LivenessFace"}

    result = await ark_asset_delete(AssetDeleteInput(group_id="live-1", confirm=True), fake_ctx)

    assert isinstance(result, AssetDeleteResult) and result.deleted is True
    list_body = next(body for action, body in asset_api.calls if action == "ListAssets")
    assert list_body["Filter"] == {"GroupIds": ["live-1"], "GroupType": "LivenessFace"}
    assert list_body["MaxResults"] == 1


async def test_delete_requires_confirmation_one_target_and_reports_missing_resource(
    asset_api: FakeAssetApi, fake_ctx: FakeContext
) -> None:
    with pytest.raises(ValidationError):
        AssetDeleteInput(asset_id="asset-1", confirm=False)
    with pytest.raises(ValidationError):
        AssetDeleteInput(group_id="group-1", confirm=False)
    with pytest.raises(ValidationError, match="exactly one of asset_id or group_id"):
        AssetDeleteInput(asset_id="asset-1", group_id="group-1", confirm=True)
    with pytest.raises(ValidationError, match="exactly one of asset_id or group_id"):
        AssetDeleteInput(confirm=True)
    assert not asset_api.calls

    missing_asset = await ark_asset_delete(
        AssetDeleteInput(asset_id="asset-missing", confirm=True), fake_ctx
    )
    missing_group = await ark_asset_delete(
        AssetDeleteInput(group_id="group-missing", confirm=True), fake_ctx
    )

    assert isinstance(missing_asset, ToolResult) and missing_asset.is_error
    assert isinstance(missing_group, ToolResult) and missing_group.is_error


async def test_group_list_by_ids_reads_each_group_and_reports_failures(
    asset_api: FakeAssetApi, fake_ctx: FakeContext
) -> None:
    asset_api.groups["live-1"] = {"Id": "live-1", "Name": "Person", "GroupType": "LivenessFace"}
    asset_api.groups["g-1"] = {"Id": "g-1", "Name": "Hero", "GroupType": "AIGC"}

    page = await ark_asset_group_list(
        AssetGroupListInput(group_ids=["live-1", "g-missing", "g-1", "live-1"]), fake_ctx
    )

    assert isinstance(page, AssetGroupPage)
    assert [group.group_id for group in page.groups] == ["live-1", "g-1"]
    assert [(error.id, error.code) for error in page.errors] == [
        ("g-missing", "NotFound.AssetGroup")
    ]
    assert not any(action == "ListAssetGroups" for action, _ in asset_api.calls)


async def test_group_list_without_ids_lists_by_filter(
    asset_api: FakeAssetApi, fake_ctx: FakeContext
) -> None:
    asset_api.groups["g-1"] = {"Id": "g-1", "Name": "Hero", "GroupType": "AIGC"}

    page = await ark_asset_group_list(AssetGroupListInput(name="Her"), fake_ctx)

    assert isinstance(page, AssetGroupPage)
    assert [group.group_id for group in page.groups] == ["g-1"]
    assert page.errors == []


def test_lookup_ids_are_validated_bounded_and_exclusive_with_filters() -> None:
    with pytest.raises(ValidationError):
        AssetGroupListInput(group_ids=["bad id!"])
    with pytest.raises(ValidationError):
        AssetGroupListInput(group_ids=[f"g-{index}" for index in range(21)])
    with pytest.raises(ValidationError, match="group_ids cannot be combined"):
        AssetGroupListInput(group_ids=["g-1"], name="Hero")
    with pytest.raises(ValidationError):
        AssetListInput(asset_ids=["not a valid id"])
    with pytest.raises(ValidationError, match="asset_ids cannot be combined"):
        AssetListInput(asset_ids=["asset-1"], statuses=["Active"])
    assert AssetListInput(asset_ids=["asset://asset-1"]).asset_ids == ["asset-1"]


async def test_asset_list_by_ids_reads_each_asset_and_reports_failures(
    asset_api: FakeAssetApi, fake_ctx: FakeContext
) -> None:
    asset_api.assets["asset-1"] = {"Id": "asset-1", "Status": "Active", "AssetType": "Image"}

    page = await ark_asset_list(AssetListInput(asset_ids=["asset://asset-1", "asset-2"]), fake_ctx)

    assert isinstance(page, AssetPage)
    assert [asset.asset_uri for asset in page.assets] == ["asset://asset-1"]
    assert [(error.id, error.code) for error in page.errors] == [("asset-2", "NotFound.Asset")]
    assert page.next_token is None


async def test_update_renames_asset_or_updates_group(
    asset_api: FakeAssetApi, fake_ctx: FakeContext
) -> None:
    asset_api.assets["asset-1"] = {"Id": "asset-1", "Status": "Active", "Name": "old"}
    asset_api.groups["g-1"] = {"Id": "g-1", "Name": "Hero", "GroupType": "AIGC"}

    renamed = await ark_asset_update(AssetUpdateInput(asset_id="asset-1", name="new"), fake_ctx)
    regrouped = await ark_asset_update(
        AssetUpdateInput(group_id="g-1", description="Main character"), fake_ctx
    )

    assert isinstance(renamed, AssetUpdateResult)
    assert renamed.asset is not None and renamed.asset.name == "new" and renamed.group is None
    assert isinstance(regrouped, AssetUpdateResult)
    assert regrouped.group is not None and regrouped.asset is None
    assert regrouped.group.description == "Main character"


def test_update_input_rules() -> None:
    with pytest.raises(ValidationError, match="description can only be updated on a group"):
        AssetUpdateInput(asset_id="asset-1", description="x")
    with pytest.raises(ValidationError, match="Provide name and/or description"):
        AssetUpdateInput(group_id="g-1")
    with pytest.raises(ValidationError, match="exactly one of asset_id or group_id"):
        AssetUpdateInput(name="x")


async def test_asset_create_flags_empty_group_when_every_source_fails(
    asset_api: FakeAssetApi, fake_ctx: FakeContext
) -> None:
    asset_api.fail_urls.add("https://cdn.example.com/broken.png")

    result = await ark_asset_create(
        AssetCreateInput(
            subject="Lonely Hero", sources=[{"url": "https://cdn.example.com/broken.png"}]
        ),
        fake_ctx,
    )

    assert isinstance(result, AssetCreateOutput)
    assert result.group_created is True
    assert result.group_created_empty is True


async def test_asset_waits_do_not_hold_provider_slots(
    asset_api: FakeAssetApi, fake_ctx: FakeContext
) -> None:
    asset_api.initial_status = "Processing"
    asset_api.assets["asset-ready"] = {"Id": "asset-ready", "Status": "Active"}
    waits = [
        asyncio.create_task(
            ark_asset_create(
                AssetCreateInput(
                    group_id="group-1",
                    wait_timeout_seconds=30,
                    sources=[{"url": f"https://cdn.example.com/wait-{index}.png"}],
                ),
                fake_ctx,
            )
        )
        for index in range(5)
    ]
    try:
        while sum(action == "GetAsset" for action, _ in asset_api.calls) < 5:
            await asyncio.sleep(0.01)
        await asyncio.wait_for(preflight_seedance_assets(fake_ctx, ["asset-ready"]), timeout=1.0)
    finally:
        for task in waits:
            task.cancel()
        await asyncio.gather(*waits, return_exceptions=True)


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
