"""Offline contract tests for the build-time tool documentation exporter."""

from __future__ import annotations

import inspect
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastmcp.tools import FunctionTool
from fastmcp.utilities.tasks import TaskConfig
from jsonschema import Draft202012Validator

from ark_mcp.tools._catalog import CATALOG, CatalogEntry, resolve_tool_definition

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "export_tool_docs.py"
SYNTHETIC_ENV_CANARY = "synthetic-env-canary"

GUARDS = """
import importlib.abc
import os
import runpy
import socket
import sys

import httpx
import pytest_socket
from pydantic_settings import BaseSettings

pytest_socket.disable_socket()

def forbidden(*args, **kwargs):
    raise AssertionError("Exporter attempted runtime I/O or settings construction")

socket.getaddrinfo = forbidden
httpx.Client.__init__ = forbidden
httpx.AsyncClient.__init__ = forbidden
original_settings_init = BaseSettings.__init__

def settings_init(self, *args, **kwargs):
    if type(self).__module__.startswith("ark_mcp"):
        forbidden()
    original_settings_init(self, *args, **kwargs)

BaseSettings.__init__ = settings_init

class BlockServer(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == "ark_mcp.server":
            raise AssertionError("Exporter imported the deployment entrypoint")

sys.meta_path.insert(0, BlockServer())
forbidden_files = {
    os.environ.get("ARK_MCP_ENV_FILE"),
    os.environ.get("FASTMCP_ENV_FILE"),
    os.environ.get("AWS_SHARED_CREDENTIALS_FILE"),
}

def audit(event, args):
    if event == "open":
        path, mode, flags = args
        if isinstance(path, (str, bytes, os.PathLike)):
            path = os.fsdecode(path)
            if path in forbidden_files or os.path.basename(path) == ".env":
                raise AssertionError("Exporter read an environment file")
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND):
            raise AssertionError("Exporter wrote a file")
    if event in {"os.mkdir", "os.remove", "os.rename", "os.rmdir"}:
        raise AssertionError("Exporter changed the filesystem")

sys.addaudithook(audit)

def profile(frame, event, arg):
    if (
        event == "call"
        and frame.f_globals.get("__name__") == "ark_mcp.tools._catalog"
        and frame.f_code.co_name in {
            "_always", "_modelark", "_seed3d", "_seed_audio", "_stt",
            "_object_storage", "_vod", "_omnihuman", "_assets",
            "_asset_delete",
        }
    ):
        raise AssertionError("Exporter evaluated a registration gate")

sys.setprofile(profile)
"""


def _run_export(
    directory: Path,
    environment: dict[str, str] | None = None,
    *,
    before: str = "",
    after: str = "",
) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(directory),
        "PYTHONDONTWRITEBYTECODE": "1",
        **(environment or {}),
    }
    code = GUARDS + before + f"\nrunpy.run_path({str(SCRIPT)!r}, run_name='__main__')\n" + after
    return subprocess.run(
        [sys.executable, "-I", "-B", "-c", code],
        cwd=directory,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


@pytest.fixture(scope="module")
def exported(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    result = _run_export(tmp_path_factory.mktemp("tool-docs"))
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    document: dict[str, Any] = json.loads(result.stdout)
    return document


def test_cli_exports_only_json_without_creating_files(tmp_path: Path) -> None:
    result = _run_export(tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert set(json.loads(result.stdout)) == {"tools"}
    assert result.stdout.endswith("\n")
    assert list(tmp_path.iterdir()) == []


def test_export_is_identical_with_hostile_inherited_configuration(tmp_path: Path) -> None:
    filenames = {
        ".env",
        "must-not-read-ark.env",
        "must-not-read-fastmcp.env",
        "must-not-read-credentials",
    }
    for filename in filenames:
        (tmp_path / filename).write_text("FASTMCP_LOG_LEVEL=not-a-log-level\n", encoding="utf-8")
    clean = _run_export(tmp_path)
    hostile = _run_export(
        tmp_path,
        {
            "ARK_MCP_ENV_FILE": str(tmp_path / "must-not-read-ark.env"),
            "FASTMCP_ENV_FILE": str(tmp_path / "must-not-read-fastmcp.env"),
            "FASTMCP_LOG_LEVEL": "not-a-log-level",
            "BYTEPLUS_MODELARK_API_KEY": SYNTHETIC_ENV_CANARY,
            "BYTEPLUS_MODELARK_ACCESS_KEY": SYNTHETIC_ENV_CANARY,
            "BYTEPLUS_MODELARK_3D_ENABLED": "not-a-boolean",
            "SEEDANCE_MODEL_BINDINGS": "not-json",
            "OBJECT_STORAGE_BACKEND": "not-a-backend",
            "S3_ACCESS_KEY": SYNTHETIC_ENV_CANARY,
            "AWS_SHARED_CREDENTIALS_FILE": str(tmp_path / "must-not-read-credentials"),
            "MCP_AUTH_MODE": "not-an-auth-mode",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "https://must-not-contact.invalid",
        },
    )
    assert clean.returncode == hostile.returncode == 0, clean.stderr + hostile.stderr
    assert clean.stderr == hostile.stderr == ""
    assert clean.stdout == hostile.stdout
    assert SYNTHETIC_ENV_CANARY not in hostile.stdout
    assert {path.name for path in tmp_path.iterdir()} == filenames


def test_package_shim_has_a_spec_and_never_imports_server(tmp_path: Path) -> None:
    result = _run_export(
        tmp_path,
        after="""
package = sys.modules["ark_mcp"]
assert package.__spec__.loader is None
assert package.__spec__.submodule_search_locations == package.__path__
assert package.__path__
assert "ark_mcp.tools._catalog" in sys.modules
assert "ark_mcp.server" not in sys.modules
assert "mcp" not in vars(package)
assert "BYTEPLUS_MODELARK_API_KEY" not in os.environ
assert os.environ["FASTMCP_ENV_FILE"] == os.devnull
""",
    )
    assert result.returncode == 0, result.stderr


def test_existing_application_package_is_not_replaced(tmp_path: Path) -> None:
    result = _run_export(
        tmp_path,
        before="""
from importlib.machinery import ModuleSpec
from types import ModuleType
package = ModuleType("ark_mcp")
package.__spec__ = ModuleSpec("ark_mcp", loader=object(), is_package=True)
sys.modules["ark_mcp"] = package
""",
    )
    assert result.returncode != 0
    assert result.stdout == ""
    assert "fresh Python process" in result.stderr


def test_undocumented_gate_fails_without_partial_json(tmp_path: Path) -> None:
    result = _run_export(
        tmp_path,
        before="""
original_run_path = runpy.run_path

def missing_gate(path, run_name):
    namespace = original_run_path(path)
    namespace["_REGISTRATION_GATES"].pop("_modelark")
    namespace["main"]()

runpy.run_path = missing_gate
""",
    )
    assert result.returncode != 0
    assert result.stdout == ""
    assert "Undocumented registration gate: _modelark" in result.stderr


def test_catalog_inventory_and_order_are_exact(exported: dict[str, Any]) -> None:
    assert [tool["name"] for tool in exported["tools"]] == [entry.name for entry in CATALOG]
    assert len({tool["name"] for tool in exported["tools"]}) == len(CATALOG)


def test_documentation_examples_match_actual_input_schemas(exported: dict[str, Any]) -> None:
    root = SCRIPT.parents[1]
    examples = json.loads((root / "docs/site/tool-examples.json").read_text(encoding="utf-8"))
    tools = {tool["name"]: tool for tool in exported["tools"]}
    for name, arguments in examples.items():
        assert name in tools
        Draft202012Validator(tools[name]["inputSchema"]).validate(arguments)
    for directory in ("setup", "integrations"):
        for path in (root / "docs" / directory).glob("*.md"):
            for block in re.findall(r"```json\n(.*?)\n```", path.read_text(encoding="utf-8"), re.S):
                payload = json.loads(block)
                if "name" not in payload:
                    continue
                Draft202012Validator(tools[payload["name"]]["inputSchema"]).validate(
                    payload["arguments"]
                )


def test_model_tables_only_document_catalog_tools() -> None:
    tables = json.loads(
        (SCRIPT.parents[1] / "docs/site/tool-models.json").read_text(encoding="utf-8")
    )
    assert set(tables) <= {entry.name for entry in CATALOG}
    for entry in tables.values():
        assert entry["selection"].strip()
        assert entry["models"]
        assert len({row["name"] for row in entry["models"]}) == len(entry["models"])
        for row in entry["models"]:
            assert set(row) == {"name", "id", "availability", "capabilities"}
            assert all(isinstance(value, str) and value.strip() for value in row.values())


@pytest.mark.parametrize(
    ("tool", "setting"),
    [
        ("seedream_generate_image", "seedream_default_model"),
        ("seedance_create_task", "seedance_default_model"),
        ("seed_understand", "seed_understanding_default_model"),
        ("seed_audio_understand", "seed_audio_understanding_model"),
        ("hyper3d_create_task", "hyper3d_default_model"),
        ("hitem3d_create_task", "hitem3d_default_model"),
        ("omnihuman_create_task", "omnihuman_req_key"),
    ],
)
def test_model_table_ids_match_static_configuration_defaults(tool: str, setting: str) -> None:
    from ark_mcp.config.env import Settings

    tables = json.loads(
        (SCRIPT.parents[1] / "docs/site/tool-models.json").read_text(encoding="utf-8")
    )
    assert f"`{Settings.model_fields[setting].default}`" in {
        row["id"] for row in tables[tool]["models"]
    }


def test_model_table_includes_default_flash_binding() -> None:
    from ark_mcp.config.env import SEEDANCE_2_5_FLASH_MODEL_ID

    tables = json.loads(
        (SCRIPT.parents[1] / "docs/site/tool-models.json").read_text(encoding="utf-8")
    )
    assert f"`{SEEDANCE_2_5_FLASH_MODEL_ID}`" in {
        row["id"] for row in tables["seedance_create_task"]["models"]
    }


def _canonical_schema(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: sorted(item, key=lambda option: json.dumps(option, sort_keys=True))
            if key == "enum"
            else _canonical_schema(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_canonical_schema(item) for item in value]
    return value


@pytest.mark.parametrize("entry", CATALOG, ids=lambda entry: entry.name)
def test_export_matches_fastmcp_and_catalog(exported: dict[str, Any], entry: CatalogEntry) -> None:
    definition = resolve_tool_definition(entry)
    task = (
        TaskConfig(mode=entry.background, poll_interval=timedelta(seconds=2))
        if entry.background
        else None
    )
    expected = FunctionTool.from_function(
        definition.handler,
        name=entry.name,
        output_schema=definition.output_model.model_json_schema(),
        task=task,
    )
    actual = next(tool for tool in exported["tools"] if tool["name"] == entry.name)
    assert set(actual) == {
        "name",
        "module",
        "description",
        "inputSchema",
        "outputSchema",
        "scope",
        "background",
        "registration_gate",
    }
    assert actual["description"] == expected.description == inspect.getdoc(definition.handler)
    assert actual["module"] == entry.module
    assert _canonical_schema(actual["inputSchema"]) == _canonical_schema(expected.parameters)
    assert actual["outputSchema"] == definition.output_model.model_json_schema()
    assert actual["scope"] == entry.scope
    assert actual["background"] == entry.background
    assert actual["registration_gate"].strip()
    Draft202012Validator.check_schema(actual["inputSchema"])
    Draft202012Validator.check_schema(actual["outputSchema"])
    if entry.name == "ark_job_capabilities":
        assert actual["inputSchema"]["properties"] == {}
    else:
        assert set(actual["inputSchema"]["properties"]) == {"input"}
        assert actual["inputSchema"]["required"] == ["input"]


def test_all_registration_gates_name_the_exact_environment_variables(
    exported: dict[str, Any],
) -> None:
    expected = {
        "_always": set(),
        "_modelark": {"BYTEPLUS_MODELARK_API_KEY"},
        "_seed3d": {"BYTEPLUS_MODELARK_API_KEY", "BYTEPLUS_MODELARK_3D_ENABLED"},
        "_seed_audio": {"BYTEPLUS_SEED_SPEECH_API_KEY"},
        "_stt": {"BYTEPLUS_SEED_SPEECH_API_KEY"},
        "_object_storage": {
            "OBJECT_STORAGE_BACKEND",
            "TOS_ACCESS_KEY",
            "TOS_SECRET_KEY",
            "TOS_BUCKET",
            "S3_ACCESS_KEY",
            "S3_SECRET_KEY",
            "S3_BUCKET",
        },
        "_vod": {"BYTEPLUS_VOD_MEDIAKIT_API_KEY"},
        "_omnihuman": {"BYTEPLUS_OMNIHUMAN_ENABLED", "BYTEPLUS_CV_API_KEY"},
        "_assets": {"BYTEPLUS_MODELARK_ACCESS_KEY", "BYTEPLUS_MODELARK_SECRET_KEY"},
        "_asset_delete": {
            "BYTEPLUS_MODELARK_ACCESS_KEY",
            "BYTEPLUS_MODELARK_SECRET_KEY",
            "BYTEPLUS_MODELARK_ASSETS_ALLOW_DELETE",
        },
    }
    tools = {tool["name"]: tool for tool in exported["tools"]}
    assert {entry.gate.__name__ for entry in CATALOG} == set(expected)
    for entry in CATALOG:
        gate = tools[entry.name]["registration_gate"]
        variables = set(re.findall(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b", gate))
        assert variables == expected[entry.gate.__name__], entry.name
        if entry.gate.__name__ == "_always":
            assert "always" in gate.lower()
        if entry.gate.__name__ in {"_seed3d", "_omnihuman", "_asset_delete"}:
            assert "=true" in gate
        if entry.gate.__name__ == "_object_storage":
            assert "OBJECT_STORAGE_BACKEND=tos" in gate
            assert "OBJECT_STORAGE_BACKEND=s3" in gate
            assert "default" in gate.lower()


def _schema_properties(schema: Any, path: str = "") -> Iterator[tuple[str, dict[str, Any]]]:
    if isinstance(schema, dict):
        for name, field in schema.get("properties", {}).items():
            yield f"{path}/properties/{name}", field
        for name, value in schema.items():
            yield from _schema_properties(value, f"{path}/{name}")
    elif isinstance(schema, list):
        for index, value in enumerate(schema):
            yield from _schema_properties(value, f"{path}/{index}")


def test_nested_input_and_output_fields_are_described(exported: dict[str, Any]) -> None:
    missing = []
    for tool in exported["tools"]:
        for schema_name in ("inputSchema", "outputSchema"):
            for path, field in _schema_properties(tool[schema_name]):
                if schema_name == "inputSchema" and path == "/properties/input":
                    continue
                if not field.get("description", "").strip():
                    missing.append(f"{tool['name']}.{schema_name}{path}")
    assert missing == []


@pytest.mark.parametrize(
    ("name", "schema_name", "valid", "invalid"),
    [
        ("ark_job_capabilities", "inputSchema", {}, {"input": {}}),
        ("ark_list_models", "inputSchema", {"input": {"kind": None}}, {}),
        (
            "gen_task_list",
            "inputSchema",
            {"input": {"page_size": 100}},
            {"input": {"page_size": 101}},
        ),
        (
            "media_presign",
            "inputSchema",
            {"input": {"object_keys": ["references/example"], "expires_in_seconds": 60}},
            {"input": {"object_keys": [], "expires_in_seconds": 59}},
        ),
        (
            "ark_list_models",
            "outputSchema",
            {"models": [], "services": []},
            {"models": [], "services": [{"service": "modelark", "enabled": "yes"}]},
        ),
        ("ark_job_capabilities", "outputSchema", {"targets": []}, {"targets": [{}]}),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_representative_payloads_validate_against_exported_schemas(
    exported: dict[str, Any],
    name: str,
    schema_name: str,
    valid: dict[str, Any],
    invalid: dict[str, Any],
) -> None:
    tool = next(tool for tool in exported["tools"] if tool["name"] == name)
    validator = Draft202012Validator(tool[schema_name])
    assert list(validator.iter_errors(valid)) == []
    assert list(validator.iter_errors(invalid))
