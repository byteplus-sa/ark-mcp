"""Compare the configured capability registry with an ArkModels metadata dump.

Usage:
    uv run python scripts/audit_capabilities.py path/to/arkmodels.json [--strict]

The dump may be either an ArkModels list (entries with ``Name``, ``Version``
and ``ModelMetaDatas``) or ``arkcli models get <id> --format json`` output
(objects with ``name``, ``primary_version`` and ``supported_params``), as a
single object or a list. The report is advisory: the ArkModels catalog has
known stale entries, so a human reviews drift against the official docs
before changing ``config/model_capabilities.py``. ``--strict`` exits 1 when
drift is found.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ark_mcp.config.model_capabilities import (
    ImageCapabilities,
    VideoCapabilities,
    get_capability_registry,
)


@dataclass(frozen=True)
class Finding:
    model_id: str
    parameter: str
    registry: str
    catalog: str
    status: str


def load_catalog(path: Path) -> dict[str, list[dict[str, Any]]]:
    """Return ``supported_params`` lists keyed by versioned model ID."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries = raw if isinstance(raw, list) else [raw]
    catalog: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        model_id, params = _entry_params(entry)
        if model_id and params is not None:
            catalog[model_id] = params
    return catalog


def _entry_params(entry: dict[str, Any]) -> tuple[str | None, list[dict[str, Any]] | None]:
    if "ModelMetaDatas" in entry:
        metadata = {item["Key"]: item["Data"] for item in entry["ModelMetaDatas"]}
        values = metadata.get("supported_params") or []
        params = json.loads(values[0]) if values else None
        return f"{entry['Name']}-{entry['Version']}", params
    version = entry.get("primary_version") or entry.get("version")
    name = entry.get("name")
    model_id = f"{name}-{version}" if name and version else name
    return model_id, entry.get("supported_params")


def audit(catalog: dict[str, list[dict[str, Any]]]) -> list[Finding]:
    """Compare every configured video and image binding with its catalog entry."""
    image_caps, video_caps, _, _ = get_capability_registry().configured_models()
    findings: list[Finding] = []
    for model_id, video in video_caps.items():
        findings.extend(_audit_video(model_id, video, catalog.get(model_id)))
    for model_id, image in image_caps.items():
        findings.extend(_audit_image(model_id, image, catalog.get(model_id)))
    return findings


def _audit_video(
    model_id: str, caps: VideoCapabilities, params: list[dict[str, Any]] | None
) -> list[Finding]:
    if params is None:
        return [Finding(model_id, "*", "configured", "missing", "UNKNOWN")]
    by_name = {param["name"]: param for param in params}
    return [
        _compare_enum(model_id, "resolution", caps.supported_resolutions, by_name),
        _compare_enum(model_id, "ratio", caps.supported_ratios, by_name),
        _compare_range(model_id, "duration", caps.duration_range, by_name),
        _compare_range(model_id, "priority", caps.priority_range, by_name),
        _compare_range(
            model_id, "execution_expires_after", caps.execution_expires_after_range, by_name
        ),
        _compare_enum(model_id, "omni_reference_task_type", caps.supported_task_types, by_name),
        _compare_enum(model_id, "output_format", caps.supported_output_formats, by_name),
        _compare_support(model_id, "draft", caps.supports_draft, by_name),
    ]


def _audit_image(
    model_id: str, caps: ImageCapabilities, params: list[dict[str, Any]] | None
) -> list[Finding]:
    if params is None:
        return [Finding(model_id, "*", "configured", "missing", "UNKNOWN")]
    by_name = {param["name"]: param for param in params}
    return [
        _compare_enum(model_id, "output_format", caps.supported_output_formats, by_name),
        _compare_support(model_id, "stream", caps.supports_streaming, by_name),
        _compare_support(model_id, "sequential_image_generation", caps.supports_batch, by_name),
        _compare_support(model_id, "background", caps.supports_transparent_background, by_name),
    ]


def _compare_enum(
    model_id: str, name: str, registry: tuple[str, ...], params: dict[str, dict[str, Any]]
) -> Finding:
    param = params.get(name)
    catalog = tuple(param.get("enum") or ()) if param and param.get("support") else ()
    status = "OK" if set(registry) == set(catalog) else "DRIFT"
    return Finding(model_id, name, _show(registry), _show(catalog), status)


def _compare_range(
    model_id: str, name: str, registry: tuple[int, int], params: dict[str, dict[str, Any]]
) -> Finding:
    param = params.get(name)
    if not param or not param.get("support"):
        return Finding(model_id, name, str(registry), "unsupported", "DRIFT")
    catalog = (param.get("min"), param.get("max"))
    status = "OK" if registry == catalog else "DRIFT"
    return Finding(model_id, name, str(registry), str(catalog), status)


def _compare_support(
    model_id: str, name: str, registry: bool, params: dict[str, dict[str, Any]]
) -> Finding:
    param = params.get(name)
    catalog = bool(param and param.get("support"))
    status = "OK" if registry == catalog else "DRIFT"
    return Finding(model_id, name, str(registry), str(catalog), status)


def _show(values: tuple[str, ...]) -> str:
    return ",".join(values) if values else "none"


def render(findings: list[Finding]) -> str:
    """Render findings as an aligned plain-text table."""
    header = ("model", "parameter", "registry", "catalog", "status")
    rows = [header] + [(f.model_id, f.parameter, f.registry, f.catalog, f.status) for f in findings]
    widths = [max(len(row[column]) for row in rows) for column in range(len(header))]
    return "\n".join(
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()
        for row in rows
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("catalog", type=Path, help="ArkModels or arkcli JSON dump")
    parser.add_argument("--strict", action="store_true", help="exit 1 when drift is found")
    args = parser.parse_args(argv)
    findings = audit(load_catalog(args.catalog))
    print(render(findings))
    drift = any(finding.status != "OK" for finding in findings)
    return 1 if args.strict and drift else 0


if __name__ == "__main__":
    sys.exit(main())
