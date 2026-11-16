"""Run an isolated MCP moderation lookup; offline by default, live only with --live."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import socket
from contextlib import nullcontext, redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

SETTINGS_PREFIXES = (
    "BYTEPLUS_",
    "MCP_",
    "FASTMCP_",
    "LAS_",
    "SEED_",
    "SEEDREAM_",
    "SEEDANCE_",
    "TOS_",
    "S3_",
    "OBJECT_STORAGE_",
    "ARTIFACT_",
    "PROVIDER_",
    "PRINCIPAL_",
    "DAILY_",
    "MODELARK_",
    "ARK_",
    "PERSISTENCE_",
    "READINESS_",
    "RATE_LIMIT_",
    "STATE_",
    "OUTPUT_",
    "OTEL_",
)
LIVE_SETTINGS = (
    "BYTEPLUS_MODELARK_ACCESS_KEY",
    "BYTEPLUS_MODELARK_SECRET_KEY",
    "BYTEPLUS_MODELARK_SESSION_TOKEN",
    "BYTEPLUS_MODELARK_MODERATION_ENABLED",
    "BYTEPLUS_MODELARK_OPENAPI_BASE_URL",
    "BYTEPLUS_MODELARK_REGION",
)
FAKE_BASE = "https://moderation.example.test"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--offline", action="store_true", help="Use a synthetic provider (default).")
    modes.add_argument(
        "--live", action="store_true", help="One authorized lookup of an existing block."
    )
    return parser.parse_args(argv)


def configure_environment(directory: Path, *, live: bool) -> None:
    values = {key: os.environ[key] for key in LIVE_SETTINGS if key in os.environ} if live else {}
    for key in list(os.environ):
        if key.startswith(SETTINGS_PREFIXES):
            del os.environ[key]
    empty_env = directory / "empty.env"
    empty_env.touch()
    os.environ.update(
        ARK_MCP_ENV_FILE=str(empty_env),
        ARTIFACT_DIR=str(directory / "artifacts"),
        BYTEPLUS_MODELARK_PRICING_REFRESH_SECONDS="0",
        MCP_AUTH_MODE="local",
        ARK_LOG_LEVEL="ERROR",
    )
    os.environ.update(values)
    if not live:
        os.environ.update(
            BYTEPLUS_MODELARK_ACCESS_KEY="synthetic-access",
            BYTEPLUS_MODELARK_SECRET_KEY="synthetic-secret",  # pragma: allowlist secret
            BYTEPLUS_MODELARK_SESSION_TOKEN="synthetic-session",
            BYTEPLUS_MODELARK_MODERATION_ENABLED="true",
            BYTEPLUS_MODELARK_OPENAPI_BASE_URL=FAKE_BASE,
        )


def error_code(result: Any) -> str:
    text = " ".join(getattr(block, "text", "") for block in result.content)
    match = re.search(r"\[code=([A-Za-z0-9._-]{1,80})(?:,|\])", text)
    return match.group(1) if match else "MCP_ERROR"


async def lookup(*, live: bool) -> dict[str, object]:
    from fastmcp import Client

    from ark_mcp.config.env import get_settings
    from ark_mcp.server import create_server
    from ark_mcp.tools.ark_moderation_get import ModerationGetInput

    settings = get_settings()
    if not settings.has_modelark_moderation:
        return {"status": "fail", "code": "CONFIGURATION_REQUIRED"}
    record_id = os.environ.get("MODERATION_SMOKE_ID", "") if live else "synthetic-block"
    record_type = os.environ.get("MODERATION_SMOKE_TYPE", "") if live else "task_id"
    arguments = ModerationGetInput.model_validate({"id": record_id, "type": record_type})
    async with Client(create_server(settings)) as client:
        result = await client.call_tool(
            "ark_moderation_get", {"input": arguments.model_dump()}, raise_on_error=False
        )
        if result.is_error:
            return {"status": "fail", "code": error_code(result)}
        count = len((result.structured_content or {}).get("block_reasons", []))
        if not count:
            return {"status": "fail", "code": "NO_BLOCK_REASONS", "count": 0}
        if not live:
            missing = await client.call_tool(
                "ark_moderation_get",
                {"input": {"id": "synthetic-missing", "type": "asset_id"}},
                raise_on_error=False,
            )
            if not missing.is_error or error_code(missing) != "NotFound.Id":
                return {"status": "fail", "code": "ERROR_CONTRACT"}
        return {"status": "pass", "mode": "live" if live else "offline", "count": count}


async def run_offline() -> dict[str, object]:
    import httpx
    import respx

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.params.get("Action") != "GetModerationResult":
            raise RuntimeError("Unexpected action")
        if set(body) != {"Id", "Type"} or "X-Security-Token" not in request.headers:
            raise RuntimeError("Invalid signed request")
        if body["Id"] == "synthetic-missing":
            return httpx.Response(
                404,
                json={
                    "ResponseMetadata": {"Error": {"Code": "NotFound.Id", "Message": "Not found"}}
                },
            )
        return httpx.Response(
            200,
            json={"Result": {"block_reasons": [{"label": "Safety", "sub_label": "Synthetic"}]}},
        )

    with respx.mock(assert_all_mocked=True) as router:
        route = router.post(f"{FAKE_BASE}/").mock(side_effect=respond)
        result = await lookup(live=False)
        if route.call_count != 2:
            return {"status": "fail", "code": "REQUEST_COUNT"}
        return result


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    connect = socket.socket.connect

    def offline_connect(connection: socket.socket, address: Any) -> None:
        if connection.family != socket.AF_UNIX:
            raise RuntimeError("Offline smoke forbids external sockets")
        connect(connection, address)

    try:
        with TemporaryDirectory(prefix="ark-moderation-smoke-") as directory:
            configure_environment(Path(directory), live=args.live)
            guard = nullcontext() if args.live else patch("socket.socket.connect", offline_connect)
            with guard, open(os.devnull, "w") as discarded, redirect_stderr(discarded):
                result = asyncio.run(lookup(live=True) if args.live else run_offline())
    except Exception as exc:
        result = {"status": "fail", "code": type(exc).__name__}
    print(json.dumps(result))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
