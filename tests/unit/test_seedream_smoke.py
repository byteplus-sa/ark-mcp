from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastmcp import Client, FastMCP

from ark_mcp.config.env import get_settings

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "smoke_seedream55.py"
MODEL = "dola-seedream-5-5-261028"
PRO_MODEL = "dola-seedream-5-0-pro-260628"
JOB_ID = "job_" + "a" * 24


@pytest.fixture
def smoke():
    spec = importlib.util.spec_from_file_location("seedream_smoke_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("BYTEPLUS_MODELARK_API_KEY", "unit-test-only")
    monkeypatch.setenv(
        "SEEDREAM_MODEL_BINDINGS",
        json.dumps(
            [
                {"model_id": MODEL, "family": "seedream_5_5"},
                {"model_id": PRO_MODEL, "family": "pro"},
            ]
        ),
    )
    get_settings.cache_clear()
    return get_settings()


def image_bytes(format="PNG", transparent=False, size=(1024, 1024)):
    from PIL import Image

    image = Image.new("RGBA" if transparent else "RGB", size, (255, 0, 0))
    if transparent:
        image.putpixel((0, 0), (0, 0, 0, 0))
    stream = BytesIO()
    image.save(stream, format=format)
    return stream.getvalue()


def generation(data, format="jpeg", metadata=None):
    artifact_id = str(uuid4())
    return {
        "model": MODEL,
        "created_at": "2026-11-10T00:00:00Z",
        "artifacts": [
            {
                "id": artifact_id,
                "uri": f"seed-media://artifacts/{artifact_id}",
                "media_type": "image",
                "mime_type": f"image/{format}",
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "created_at": "2026-11-10T00:00:00Z",
            }
        ],
        "generations": [
            {
                "variation_index": 0,
                "safety_identifier": "do-not-report-this",
                "images": [{"index": 0, "artifact_id": artifact_id, **(metadata or {})}],
            }
        ],
    }


def response(payload, error=False):
    return SimpleNamespace(structured_content=payload, is_error=error)


def accepted():
    return {
        "job_id": JOB_ID,
        "target_tool": "seedream_generate_image",
        "status": "working",
        "created_at": "2026-11-10T00:00:00Z",
        "ttl_ms": 60000,
        "poll_after_ms": 0,
    }


def snapshot(status="completed", output=None, is_error=False):
    return {
        **accepted(),
        "status": status,
        "updated_at": "2026-11-10T00:00:00Z",
        "result": (
            {"structured_content": output, "is_error": is_error} if output is not None else None
        ),
    }


def test_default_and_help_are_offline_and_write_nothing(smoke, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(smoke, "execute", AsyncMock(side_effect=AssertionError("live")))
    assert smoke.main([]) == 0
    assert "--execute" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        smoke.main(["--help"])
    assert exc.value.code == 0
    assert not list(tmp_path.iterdir())
    smoke.execute.assert_not_called()


@pytest.mark.parametrize(
    "arguments",
    [
        ["--execute"],
        ["--execute", "--confirm-cost", "0"],
        ["--execute", "--confirm-cost", "-1"],
        ["--execute", "--confirm-cost", "nan"],
        ["--execute", "--confirm-cost", "inf"],
        ["--execute", "--confirm-cost", "1", "--max-cases", "0"],
        ["--execute", "--confirm-cost", "1", "--max-cases", "7"],
        ["--execute", "--confirm-cost", "1", "--timeout", "0"],
        ["--execute", "--confirm-cost", "1", "--timeout", "nan"],
        ["--execute", "--confirm-cost", "1", "--poll-interval", "0"],
        ["--execute", "--confirm-cost", "1", "--max-polls", "0"],
    ],
)
def test_execution_gate_rejects_invalid_flags_before_client(smoke, arguments, monkeypatch):
    monkeypatch.setattr(smoke, "execute", AsyncMock(side_effect=AssertionError("live")))
    with pytest.raises(SystemExit) as exc:
        smoke.main(arguments)
    assert exc.value.code == 2
    smoke.execute.assert_not_called()


def test_matrix_uses_synthetic_references_and_explicit_pro(smoke, settings):
    from PIL import Image

    cases = smoke.build_cases(settings)
    assert [case.name for case in cases] == list(smoke.BASE_CASES)
    assert cases[0].request.images is None
    assert cases[1].request.images is None
    assert cases[1].request.background == "transparent"
    for case in cases[2:]:
        reference = case.request.images[0]
        with Image.open(BytesIO(base64.b64decode(reference.data))) as image:
            assert image.format == "PNG"
            assert image.size == (1024, 1024)
            assert image.mode == "RGB"
    assert cases[3].request.point.x == 500
    assert cases[4].request.bbox.x1 == 250
    cases = smoke.build_cases(settings, pro_model=PRO_MODEL, max_cases=6)
    pro = cases[-1]
    assert pro.name == "pro-transparent"
    assert pro.request.model == PRO_MODEL
    with Image.open(BytesIO(base64.b64decode(pro.request.images[0].data))) as image:
        assert image.getextrema()[3] == (0, 255)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"model": PRO_MODEL},
        {"model": "not-configured"},
        {"pro_model": "not-configured", "max_cases": 6},
        {"pro_model": MODEL, "max_cases": 6},
        {"pro_model": PRO_MODEL},
        {"max_cases": 1},
        {"selected": ["t2i-opaque", "t2i-opaque"]},
        {"selected": ["pro-transparent"]},
        {"selected": []},
        {"selected": ["not-a-case"]},
    ],
)
def test_matrix_fails_closed_for_bindings_selection_and_case_cap(smoke, settings, kwargs):
    with pytest.raises(smoke.SmokeFailure):
        smoke.build_cases(settings, **kwargs)


def test_matrix_selection_limits_paid_cases(smoke, settings):
    cases = smoke.build_cases(settings, selected=["bbox-edit"], max_cases=1)
    assert len(cases) == 1
    assert cases[0].name == "bbox-edit"


def test_output_directory_rejects_traversal_symlinks_and_source_tree(smoke, tmp_path):
    repo = SCRIPT.parent.parent
    for path in [repo, repo / "scripts", repo / "tests", repo / ".git", Path("/"), tmp_path / ".."]:
        with pytest.raises(smoke.SmokeFailure):
            smoke.safe_output_directory(path)
    (tmp_path / "link").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(smoke.SmokeFailure):
        smoke.safe_output_directory(tmp_path / "link" / "output")
    assert smoke.safe_output_directory(tmp_path / "output") == tmp_path / "output"
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize(
    ("format", "transparent", "case_name"),
    [
        ("JPEG", False, "t2i-opaque"),
        ("PNG", True, "t2i-transparent"),
        ("PNG", False, "reference-edit"),
    ],
)
async def test_persisted_bytes_verify_format_dimensions_alpha_and_metadata(
    smoke, settings, tmp_path, format, transparent, case_name
):
    case = smoke.build_cases(settings, selected=[case_name], max_cases=1)[0]
    data = image_bytes(format, transparent)
    output = generation(data, format.lower())
    client = SimpleNamespace(
        read_resource=AsyncMock(
            return_value=[
                SimpleNamespace(
                    blob=base64.b64encode(data).decode(), mime_type=f"image/{format.lower()}"
                )
            ]
        )
    )
    result = await smoke.verify_output(client, case, output, tmp_path, timeout=1)
    assert result["observed"]["width"] == 1024
    assert result["observed"]["has_transparency"] is transparent
    assert result["provider_metadata"] == {"size": None, "output_format": None, "background": None}
    assert result["requested"]["output_format"] == format.lower()
    saved = tmp_path / result["file"]
    assert saved.read_bytes() == data
    assert "do-not-report-this" not in json.dumps(result)


@pytest.mark.parametrize(
    "problem",
    [
        "empty",
        "corrupt",
        "wrong-dimensions",
        "wrong-format",
        "no-alpha",
        "all-transparent",
        "unpersisted",
        "digest",
        "byte-count",
        "mime",
        "item-error",
        "missing",
        "extra-image",
        "metadata-url",
    ],
)
async def test_output_validation_rejects_invalid_persistence_and_media(
    smoke, settings, tmp_path, problem
):
    case = smoke.build_cases(settings, selected=["t2i-transparent"], max_cases=1)[0]
    data = image_bytes("PNG", True)
    if problem == "empty":
        data = b""
    elif problem == "corrupt":
        data = b"not an image"
    elif problem == "wrong-dimensions":
        data = image_bytes("PNG", True, (1, 1))
    elif problem == "wrong-format":
        data = image_bytes("JPEG")
    elif problem == "no-alpha":
        data = image_bytes("PNG")
    elif problem == "all-transparent":
        from PIL import Image

        stream = BytesIO()
        Image.new("RGBA", (1024, 1024), (0, 0, 0, 0)).save(stream, format="PNG")
        data = stream.getvalue()
    output = generation(data, "png")
    artifact = output["artifacts"][0]
    if problem == "unpersisted":
        artifact["uri"] = "https://example.invalid/secret?token=private"
    elif problem == "digest":
        artifact["sha256"] = "0" * 64
    elif problem == "byte-count":
        artifact["bytes"] += 1
    elif problem == "mime":
        artifact["mime_type"] = "image/jpeg"
    elif problem == "item-error":
        output["item_errors"] = [{"index": 0, "message": "secret"}]
    elif problem == "missing":
        output["artifacts"] = []
    elif problem == "extra-image":
        output["artifacts"].append(dict(artifact))
    elif problem == "metadata-url":
        output["generations"][0]["images"][0]["size"] = "https://private.invalid/secret"
    client = SimpleNamespace(
        read_resource=AsyncMock(
            return_value=[
                SimpleNamespace(blob=base64.b64encode(data).decode(), mime_type="image/png")
            ]
        )
    )
    with pytest.raises(smoke.SmokeFailure) as exc:
        await smoke.verify_output(client, case, output, tmp_path, timeout=1)
    assert "secret" not in str(exc.value)
    assert not list(tmp_path.iterdir())


async def test_job_uses_public_tool_calls_and_waits_for_terminal_result(smoke):
    server = FastMCP("seedream-smoke-offline")
    calls = []

    @server.tool
    async def ark_job_submit(input: dict) -> dict:
        calls.append(("submit", input))
        return accepted()

    @server.tool
    async def ark_job_get(input: dict) -> dict:
        calls.append(("get", input))
        return snapshot(output={"dry_run": True})

    recorded = []
    async with Client(server) as client:
        output = await smoke.run_job(
            client,
            {"dry_run": True},
            timeout=1,
            interval=0.001,
            max_polls=3,
            on_accept=recorded.append,
        )
    assert output == {"dry_run": True}
    assert recorded == [JOB_ID]
    assert calls == [
        (
            "submit",
            {"tool_name": "seedream_generate_image", "arguments": {"input": {"dry_run": True}}},
        ),
        ("get", {"job_id": JOB_ID}),
    ]


@pytest.mark.parametrize(
    "failure",
    [
        "submit",
        "poll",
        "failed",
        "cancelled",
        "input_required",
        "tool-error",
        "missing-result",
        "timeout",
        "poll-limit",
    ],
)
async def test_job_errors_timeouts_and_poll_limit_never_resubmit(smoke, failure):
    async def call(name, arguments, **kwargs):
        if name == "ark_job_submit":
            if failure == "submit":
                raise RuntimeError("https://private.invalid/?token=secret")
            return response(accepted())
        if failure == "poll":
            raise RuntimeError("private-secret-content")
        if failure in {"failed", "cancelled", "input_required"}:
            return response(snapshot(failure))
        if failure == "tool-error":
            return response(snapshot(output={"detail": "private-secret-content"}, is_error=True))
        if failure == "missing-result":
            return response(snapshot())
        return response(snapshot("working"))

    client = SimpleNamespace(call_tool=AsyncMock(side_effect=call))
    with pytest.raises(smoke.SmokeFailure) as exc:
        await smoke.run_job(
            client,
            {},
            timeout=0.005 if failure == "timeout" else 1,
            interval=0.001,
            max_polls=2 if failure == "poll-limit" else 100,
        )
    assert "secret" not in str(exc.value)
    assert sum(call.args[0] == "ark_job_submit" for call in client.call_tool.call_args_list) == 1
    if failure != "submit":
        assert exc.value.job_id == JOB_ID


@pytest.mark.parametrize("estimate", [0.2, -1, float("nan"), float("inf")])
async def test_cost_gate_blocks_all_paid_work_and_sanitizes_report(
    smoke, settings, tmp_path, monkeypatch, capsys, estimate
):
    cases = smoke.build_cases(settings, selected=["t2i-opaque"], max_cases=1)
    seen = []

    async def job(client, arguments, **kwargs):
        seen.append(arguments)
        return {
            "dry_run": True,
            "preview": {
                "estimated_cost_usd": estimate,
                "requests": [
                    {"prompt": "private-prompt", "image": "https://private.invalid/?token=secret"}
                ],
            },
        }

    monkeypatch.setattr(smoke, "run_job", job)
    assert (
        await smoke.run_matrix(
            None, cases, tmp_path, confirmed_cost=0.1, timeout=1, interval=0.001, max_polls=3
        )
        == 1
    )
    assert all(arguments["dry_run"] for arguments in seen)
    text = (tmp_path / "report.json").read_text()
    assert not any(value in text for value in ["private-prompt", "https://", "secret"])
    if estimate == 0.2:
        assert "0.2000" in capsys.readouterr().out


async def test_matrix_prices_every_case_before_billing_and_journals_handle(
    smoke, settings, tmp_path, monkeypatch, capsys
):
    cases = smoke.build_cases(settings, selected=["t2i-opaque", "t2i-transparent"], max_cases=2)
    seen = []

    async def job(client, arguments, **kwargs):
        seen.append(arguments["dry_run"])
        if arguments["dry_run"]:
            return {"dry_run": True, "preview": {"estimated_cost_usd": 0.03}}
        assert "0.0600" in capsys.readouterr().out or len(seen) == 4
        kwargs["on_accept"](JOB_ID)
        assert JOB_ID in (tmp_path / "report.json").read_text()
        return {"dry_run": False}

    async def verify(client, case, output, directory, **kwargs):
        return {"file": f"{case.name}.png", "observed": {"width": 1024}}

    monkeypatch.setattr(smoke, "run_job", job)
    monkeypatch.setattr(smoke, "verify_output", verify)
    assert (
        await smoke.run_matrix(
            None, cases, tmp_path, confirmed_cost=0.06, timeout=1, interval=0.001, max_polls=3
        )
        == 0
    )
    assert seen == [True, True, False, False]
    report = json.loads((tmp_path / "report.json").read_text())
    assert [case["status"] for case in report["cases"]] == ["passed", "passed"]


async def test_matrix_stops_after_ambiguous_paid_failure(smoke, settings, tmp_path, monkeypatch):
    cases = smoke.build_cases(settings, selected=["t2i-opaque", "t2i-transparent"], max_cases=2)
    seen = []

    async def job(client, arguments, **kwargs):
        seen.append(arguments["dry_run"])
        if arguments["dry_run"]:
            return {"dry_run": True, "preview": {"estimated_cost_usd": 0.03}}
        kwargs["on_accept"](JOB_ID)
        raise smoke.SmokeFailure("poll_timeout", job_id=JOB_ID)

    monkeypatch.setattr(smoke, "run_job", job)
    assert (
        await smoke.run_matrix(
            None, cases, tmp_path, confirmed_cost=0.1, timeout=1, interval=0.001, max_polls=3
        )
        == 1
    )
    assert seen == [True, True, False]
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["cases"][0]["job_id"] == JOB_ID
    assert report["cases"][1]["status"] == "not_run"


@pytest.mark.parametrize("stage", ["submit", "poll"])
async def test_job_deadline_bounds_a_hung_tool_call(smoke, stage):
    import asyncio

    async def call(name, arguments, **kwargs):
        if name == "ark_job_submit" and stage == "poll":
            return response(accepted())
        await asyncio.Event().wait()

    client = SimpleNamespace(call_tool=AsyncMock(side_effect=call))
    with pytest.raises(smoke.SmokeFailure) as exc:
        await smoke.run_job(client, {}, timeout=0.02, interval=0.001, max_polls=10)
    assert exc.value.code == ("submission_unknown" if stage == "submit" else "poll_timeout")
    assert sum(call.args[0] == "ark_job_submit" for call in client.call_tool.call_args_list) == 1


async def test_metadata_echo_stays_distinct_from_local_observations(smoke, settings, tmp_path):
    case = smoke.build_cases(settings, selected=["reference-edit"], max_cases=1)[0]
    data = image_bytes("PNG")
    metadata = {"size": "960x960", "output_format": "jpeg", "background": "transparent"}
    output = generation(data, "png", metadata)
    client = SimpleNamespace(
        read_resource=AsyncMock(
            return_value=[
                SimpleNamespace(blob=base64.b64encode(data).decode(), mime_type="image/png")
            ]
        )
    )
    result = await smoke.verify_output(client, case, output, tmp_path, timeout=1)
    assert result["provider_metadata"] == metadata
    assert result["observed"]["width"] == 1024
    assert result["observed"]["output_format"] == "png"
    assert result["observed"]["has_transparency"] is False


@pytest.mark.parametrize("include_pro", [False, True])
async def test_execute_runs_full_matrix_in_real_worker_without_network(
    smoke, settings, tmp_path, monkeypatch, include_pro, capsys
):
    from fastmcp.server.dependencies import get_context

    from ark_mcp.config.model_capabilities import refresh_capability_registry
    from ark_mcp.providers.modelark.schemas import SeedreamProviderResponse
    from ark_mcp.providers.modelark.seedream import SeedreamService

    refresh_capability_registry()
    requests = []

    async def generate(service, request):
        assert get_context().task_id is not None
        if not requests:
            assert "Aggregate estimated price:" in capsys.readouterr().out
        requests.append(request)
        data = image_bytes(
            "JPEG" if request.output_format == "jpeg" else "PNG",
            request.background == "transparent",
        )
        return SeedreamProviderResponse(
            data=[{"b64_json": base64.b64encode(data).decode()}],
            safety_identifier="private-provider-echo",
        ), "private-request-trace"

    monkeypatch.setattr(SeedreamService, "generate", generate)
    arguments = [
        "--execute",
        "--confirm-cost",
        "1",
        "--output-dir",
        str(tmp_path),
        "--timeout",
        "15",
        "--max-polls",
        "10",
    ]
    if include_pro:
        arguments.extend(["--pro-model", PRO_MODEL, "--max-cases", "6"])
    assert await smoke.execute(smoke.parser().parse_args(arguments)) == 0
    expected_count = 6 if include_pro else 5
    assert len(requests) == expected_count
    assert requests[3].prompt.startswith("Image 1<point>500 500</point>")
    assert requests[4].prompt.startswith("Image 1<bbox>250 250 750 750</bbox>")
    if include_pro:
        assert requests[-1].model == PRO_MODEL
    report_path = next(tmp_path.glob("seedream55-*/report.json"))
    report = json.loads(report_path.read_text())
    assert len(report["cases"]) == expected_count
    assert all(case["status"] == "passed" for case in report["cases"])
    assert all(
        case["provider_metadata"] == {"size": None, "output_format": None, "background": None}
        for case in report["cases"]
    )
    assert all((report_path.parent / case["file"]).is_file() for case in report["cases"])
    assert not any(
        value in report_path.read_text()
        for value in [
            "private-provider-echo",
            "private-request-trace",
            "prompt",
            "url",
            "unit-test-only",
        ]
    )


@pytest.mark.parametrize("arguments", [[], ["--execute"], ["--execute", "--confirm-cost", "nan"]])
async def test_direct_execute_also_requires_cost_gate(smoke, monkeypatch, arguments):
    monkeypatch.setattr(
        "ark_mcp.config.env.get_settings",
        lambda: pytest.fail("Settings must not be loaded before the explicit execution gate."),
    )
    with pytest.raises(smoke.SmokeFailure, match="cost_confirmation_required"):
        await smoke.execute(smoke.parser().parse_args(arguments))


async def test_execute_forces_private_filesystem_artifacts(smoke, settings, tmp_path, monkeypatch):
    configured = settings.model_copy(update={"artifact_backend": "object_storage"})
    monkeypatch.setattr("ark_mcp.config.env.get_settings", lambda: configured)
    observed = []

    def capture_server(runtime_settings):
        observed.append(runtime_settings)
        raise smoke.SmokeFailure("captured_before_client")

    monkeypatch.setattr("ark_mcp.server.create_server", capture_server)
    args = smoke.parser().parse_args(
        ["--execute", "--confirm-cost", "1", "--output-dir", str(tmp_path)]
    )
    with pytest.raises(smoke.SmokeFailure, match="captured_before_client"):
        await smoke.execute(args)
    assert len(observed) == 1
    assert observed[0].artifact_backend == "filesystem"
    assert Path(observed[0].artifact_dir).is_relative_to(tmp_path)
    assert observed[0].modelark_pricing_refresh_seconds == 0
