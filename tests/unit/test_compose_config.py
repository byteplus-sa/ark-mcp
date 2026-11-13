"""Offline deployment invariants for the single-organization Compose kit."""

from pathlib import Path

import yaml

from ark_mcp.config.env import Settings


def test_application_stop_grace_exceeds_default_request_timeout() -> None:
    root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load((root / "deploy/compose/compose.yaml").read_text())
    grace = compose["services"]["ark-mcp"].get("stop_grace_period")
    assert grace is not None
    assert grace.endswith("s")
    seconds = int(grace[:-1])
    assert seconds >= Settings(_env_file=None).request_timeout_ms / 1000 + 60
    assert f"`{grace}`" in (root / "docs/self-hosting.md").read_text()
