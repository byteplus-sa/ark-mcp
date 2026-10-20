import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def workflow(name: str) -> dict:
    return yaml.load((ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader)


def test_publisher_validates_exact_tag_commit_before_writing() -> None:
    publisher = workflow("publish-release.yml")
    assert set(publisher["on"]) == {"workflow_dispatch"}
    assert publisher["permissions"] == {"contents": "read"}
    resolve = publisher["jobs"]["resolve"]
    assert resolve["permissions"] == {"contents": "write"}
    assert resolve["if"] == "github.ref == 'refs/heads/main'"
    assert "git merge-base --is-ancestor" in resolve["steps"][-1]["run"]
    assert "isDraft" in resolve["steps"][-1]["run"]
    validate = publisher["jobs"]["validate"]
    assert validate["uses"] == "./.github/workflows/ci.yml"
    assert validate["with"]["ref"] == "${{ needs.resolve.outputs.sha }}"
    publish = publisher["jobs"]["publish"]
    assert set(publish["needs"]) == {"resolve", "validate", "security"}
    assert publisher["jobs"]["security"]["with"]["ref"] == "${{ needs.resolve.outputs.sha }}"
    assert publish["steps"][0]["with"]["ref"] == "${{ needs.resolve.outputs.sha }}"
    assert "--draft=false" in publish["steps"][-1]["run"]


def test_ci_can_validate_release_prs_and_exact_release_commits() -> None:
    ci = workflow("ci.yml")
    assert "workflow_dispatch" in ci["on"]
    assert ci["on"]["workflow_call"]["inputs"]["ref"]["required"] == "true"
    for job in ci["jobs"].values():
        checkout = job["steps"][0]
        assert checkout["with"]["ref"] == "${{ inputs.ref || github.sha }}"
    assert ci["jobs"]["container"]["needs"] == "quality"
    push = ci["jobs"]["container"]["steps"][-1]
    assert "github.event_name == 'push'" in push["if"]
    assert ":edge" in push["with"]["tags"]
    assert ":latest" not in push["with"]["tags"]


def test_bot_generated_events_have_explicit_workflow_dispatches() -> None:
    release = workflow("release-please.yml")["jobs"]["release"]
    assert release["permissions"]["actions"] == "write"
    commands = "\n".join(step.get("run", "") for step in release["steps"])
    assert 'gh workflow run ci.yml --ref "$branch"' in commands
    assert 'gh workflow run codeql.yml --ref "$branch"' in commands
    assert 'gh workflow run publish-release.yml --ref main -f tag="$RELEASE_TAG"' in commands


def test_python_releases_update_only_root_lockfile_package() -> None:
    config = json.loads((ROOT / "release-please-config.json").read_text())["packages"]["."]
    assert config["release-type"] == "python"
    assert config.get("release-as") in {None, "0.2.0"}
    assert config["draft"] is True
    assert config["force-tag-creation"] is True
    assert config["include-component-in-tag"] is False
    assert config["extra-files"] == [
        {
            "type": "toml",
            "path": "uv.lock",
            "jsonpath": "$.package[?(@.name.value=='ark-mcp')].version",
        }
    ]
