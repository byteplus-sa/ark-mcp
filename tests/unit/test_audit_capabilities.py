"""Tests for the offline capability drift audit script."""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from ark_mcp.config.env import get_settings
from ark_mcp.config.model_capabilities import refresh_capability_registry

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "audit_capabilities.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("audit_capabilities", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


audit_script = _load_script()

SEEDANCE_2_5_PARAMS = [
    {"name": "resolution", "support": True, "enum": ["480p", "720p", "1080p"]},
    {
        "name": "ratio",
        "support": True,
        "enum": ["16:9", "4:3", "1:1", "3:4", "9:16", "21:9", "adaptive"],
    },
    {"name": "duration", "support": True, "min": 4, "max": 30},
    {"name": "priority", "support": True, "min": 0, "max": 9},
    {"name": "execution_expires_after", "support": True, "min": 3600, "max": 259200},
    {
        "name": "omni_reference_task_type",
        "support": True,
        "enum": ["auto", "reference", "edit", "extend"],
    },
    {"name": "output_format", "support": True, "enum": ["mp4", "mov"]},
    {"name": "draft", "support": True},
]


@pytest.fixture
def seedance_2_5_only(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv(
        "SEEDANCE_MODEL_BINDINGS",
        '[{"model_id":"dreamina-seedance-2-5-260628","family":"seedance_2_5"}]',
    )
    monkeypatch.setenv("SEEDANCE_DEFAULT_MODEL", "dreamina-seedance-2-5-260628")
    get_settings.cache_clear()
    refresh_capability_registry()
    yield
    get_settings.cache_clear()
    refresh_capability_registry()


def _arkmodels_entry(name: str, version: str, params: list[dict[str, object]]) -> dict[str, object]:
    return {
        "Name": name,
        "Version": version,
        "ModelMetaDatas": [{"Key": "supported_params", "Data": [json.dumps(params)]}],
    }


def test_loads_arkmodels_list_shape(tmp_path: Path) -> None:
    path = tmp_path / "arkmodels.json"
    path.write_text(json.dumps([_arkmodels_entry("dreamina-seedance-2-5", "260628", [])]))
    assert audit_script.load_catalog(path) == {"dreamina-seedance-2-5-260628": []}


def test_loads_arkcli_models_get_shape(tmp_path: Path) -> None:
    path = tmp_path / "model.json"
    path.write_text(
        json.dumps(
            {"name": "dreamina-seedance-2-5", "primary_version": "260628", "supported_params": []}
        )
    )
    assert audit_script.load_catalog(path) == {"dreamina-seedance-2-5-260628": []}


def test_matching_catalog_reports_no_video_drift(seedance_2_5_only: None) -> None:
    findings = audit_script.audit({"dreamina-seedance-2-5-260628": SEEDANCE_2_5_PARAMS})
    video = [f for f in findings if f.model_id == "dreamina-seedance-2-5-260628"]
    assert video
    assert {f.status for f in video} == {"OK"}


def test_changed_catalog_values_are_reported_as_drift(seedance_2_5_only: None) -> None:
    params = [dict(p) for p in SEEDANCE_2_5_PARAMS]
    params[2] = {"name": "duration", "support": True, "min": 1, "max": 30}
    params[7] = {"name": "draft", "support": False}
    findings = audit_script.audit({"dreamina-seedance-2-5-260628": params})
    drift = {f.parameter for f in findings if f.status == "DRIFT"}
    assert drift == {"duration", "draft"}


def test_missing_catalog_entry_is_unknown(seedance_2_5_only: None) -> None:
    findings = audit_script.audit({})
    assert {f.status for f in findings} == {"UNKNOWN"}


def test_strict_mode_exits_nonzero_on_drift(
    seedance_2_5_only: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "arkmodels.json"
    path.write_text(json.dumps([_arkmodels_entry("dreamina-seedance-2-5", "260628", [])]))
    assert audit_script.main([str(path)]) == 0
    assert audit_script.main([str(path), "--strict"]) == 1
    output = capsys.readouterr().out
    assert output.splitlines()[0].split() == ["model", "parameter", "registry", "catalog", "status"]
