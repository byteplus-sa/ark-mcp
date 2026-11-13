import json
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement

REPOSITORY = Path(__file__).resolve().parents[2]


def _fastmcp_requirement(dependencies: list[str]) -> Requirement:
    requirements = [
        requirement
        for dependency in dependencies
        if (requirement := Requirement(dependency)).name == "fastmcp"
    ]
    assert len(requirements) == 1
    return requirements[0]


def test_declarative_fastmcp_requirement_matches_manifest() -> None:
    manifest = tomllib.loads((REPOSITORY / "pyproject.toml").read_text())
    configuration = json.loads((REPOSITORY / "fastmcp.json").read_text())

    declared = _fastmcp_requirement(configuration["environment"]["dependencies"])
    supported = _fastmcp_requirement(manifest["project"]["dependencies"])

    assert declared == supported
    assert declared.extras == {"tasks"}


@pytest.mark.parametrize(
    ("version", "supported"),
    [("4.0.3", False), ("4.1.7", False), ("4.1.8", True), ("4.2.0", False), ("5.0.0", False)],
)
def test_declarative_fastmcp_versions_enforce_security_floor_and_minor_ceiling(
    version: str, supported: bool
) -> None:
    configuration = json.loads((REPOSITORY / "fastmcp.json").read_text())
    requirement = _fastmcp_requirement(configuration["environment"]["dependencies"])

    assert requirement.specifier.contains(version) is supported
