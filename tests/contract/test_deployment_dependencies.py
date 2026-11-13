from __future__ import annotations

import json
import tomllib
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[2]


def test_declarative_seedream_inspector_dependency_matches_project_requirement() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    configuration = json.loads((ROOT / "fastmcp.json").read_text())
    requirement = next(
        value
        for value in project["project"]["dependencies"]
        if Requirement(value).name.lower() == "pillow"
    )
    assert requirement in configuration["environment"]["dependencies"]
