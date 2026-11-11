---
title: Python Client
description: Use FastMCP StdioTransport for safe Ark discovery and an opt-in ordinary-job dry run.
---

Use the checkout's locked FastMCP environment, not an independently upgraded
client library. This example uses a real subprocess `StdioTransport` and
ordinary MCP tool calls with `mode="legacy"`, so no native task extension is
required. It is not a guarantee of compatibility with every future FastMCP
edition.

## Prerequisites and Scope

Complete the [shared launch prerequisites](index.md#prepare-the-launch).
Python has no user/global or project registration file here: the program
explicitly owns one server subprocess. Its checkout and private env file can
live outside the calling project.

Replace `UV`, `CHECKOUT`, and `ENV_FILE` below with actual absolute paths on
the host running Python. The environment dictionary contains only non-secret
paths/settings. The server loads the private `.env` itself; the client does
not read or print it.

Run the program with the prepared checkout's Python environment, for example:

```bash
"/absolute/path/to/uv" --directory "/absolute/path/to/ark-mcp" \
  run --locked --no-sync python "/absolute/path/to/client_example.py"
```

## Discovery and Ordinary Jobs

By default, the program only lists tools, calls `ark_list_models`, and calls
`ark_job_capabilities`. These discovery operations make no provider calls.
It disables optional readiness probes and account-pricing refresh for this
example process. The server still initializes its configured local runtime
state; use an isolated private artifact directory for a standalone preflight
as described in the [integration index](index.md#verify-without-paid-calls).

The explicit `--dry-run` option additionally submits a **non-billable**
Seedream request preview through `ark_job_submit`. It requires the Seedream
target to be enabled. The code always sets `dry_run=True`; it has no paid
generation option and never automatically falls back to generation.

```python
import argparse
import asyncio
import os
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

UV = "/absolute/path/to/uv"
CHECKOUT = "/absolute/path/to/ark-mcp"
ENV_FILE = "/absolute/path/to/private/ark-mcp/.env"


async def call(
    client: Client[StdioTransport], name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    response = await client.call_tool(name, arguments)
    if response.is_error or response.structured_content is None:
        raise RuntimeError(f"{name} did not return successful structured output")
    return response.structured_content


async def run_dry_run(
    client: Client[StdioTransport], capabilities: dict[str, Any]
) -> None:
    targets = {target["tool_name"] for target in capabilities["targets"]}
    if "seedream_generate_image" not in targets:
        raise RuntimeError("Seedream is not enabled; no job was submitted")

    accepted = await call(
        client,
        "ark_job_submit",
        {
            "input": {
                "tool_name": "seedream_generate_image",
                "arguments": {
                    "input": {
                        "prompt": "A ceramic mug on a wooden desk.",
                        "dry_run": True,
                    }
                },
            }
        },
    )
    job_id = accepted["job_id"]
    print("Ark job ID:", job_id)
    delay_ms = accepted["poll_after_ms"]

    async with asyncio.timeout(60):
        while True:
            await asyncio.sleep(delay_ms / 1000)
            snapshot = await call(
                client, "ark_job_get", {"input": {"job_id": job_id}}
            )
            if snapshot["status"] == "working":
                delay_ms = snapshot["poll_after_ms"]
                continue
            if snapshot["status"] != "completed":
                raise RuntimeError(f"Job {job_id} stopped: {snapshot['status']}")
            result = snapshot.get("result")
            if not isinstance(result, dict) or result.get("is_error"):
                raise RuntimeError(f"Job {job_id} returned a tool error")
            if not isinstance(result.get("structured_content"), dict):
                raise RuntimeError(f"Job {job_id} has no structured result")
            print("Dry run completed; no image was generated.")
            return


async def main(dry_run: bool) -> None:
    with open(os.devnull, "w") as server_stderr:
        transport = StdioTransport(
            command=UV,
            args=[
                "--directory",
                CHECKOUT,
                "run",
                "--locked",
                "--no-sync",
                "python",
                "-m",
                "ark_mcp",
            ],
            env={
                "ARK_MCP_ENV_FILE": ENV_FILE,
                "MCP_TRANSPORT": "stdio",
                "MCP_AUTH_MODE": "local",
                "BYTEPLUS_MODELARK_PRICING_REFRESH_SECONDS": "0",
                "READINESS_CHECK_PROVIDERS": "false",
            },
            keep_alive=False,
            log_file=server_stderr,
        )
        async with Client(transport, mode="legacy", timeout=30) as client:
            tools = await client.list_tools()
            models = await call(client, "ark_list_models", {"input": {}})
            capabilities = await call(client, "ark_job_capabilities", {})
            print("Connected; tool count:", len(tools))
            print("Models:", [model["model_id"] for model in models["models"]])
            print(
                "Enabled services:",
                [
                    service["service"]
                    for service in models["services"]
                    if service["enabled"]
                ],
            )
            print(
                "Job targets:",
                [target["tool_name"] for target in capabilities["targets"]],
            )
            if dry_run:
                await run_dry_run(client, capabilities)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    options = parser.parse_args()
    try:
        asyncio.run(main(options.dry_run))
    except Exception:
        raise SystemExit(
            "Connection or job failed. Inspect private local diagnostics; "
            "do not share credentials or automatically resubmit."
        ) from None
```

The example suppresses server stderr and raw exceptions to avoid accidental
disclosure. For troubleshooting, use owner-only local diagnostics and report
sanitized status/field names, not environment dumps or raw validation errors.
Empty models or targets mean discovery succeeded but the requested providers
may not be configured. Discovery does not verify key validity or entitlement.

## Result and Process Handling

Ordinary `ark_job_*` tools are the default for long operations:
`ark_job_capabilities`, `ark_job_submit`, `ark_job_get`, and `ark_job_cancel`.
Match the target's full `input_schema`; do not omit the inner `input` wrapper
from `arguments`. Check both the outer job status and `result.is_error`.
`failed`, `cancelled`, and `input_required` need explicit handling rather than
being treated as successful output.

The helper honors `poll_after_ms`, stops after 60 seconds, and does not
automatically retry submission. A timeout is not proof that nothing was
accepted. `keep_alive=False` closes the subprocess at context exit; keep it
alive until all needed job results are retrieved. Default in-memory jobs
cannot be resumed from a newly launched stdio process.

An Ark `job_id` is not a provider task ID. After an explicitly approved,
potentially billable provider-submission operation, read the provider ID
from `result.structured_content` and poll `gen_task_get`, `vod_get_task`,
or `omnihuman_get_task` as appropriate. Do not repeat a create call to poll.
`ark_job_cancel` is cooperative local cancellation, not a guarantee of
provider cancellation. Explicit file writes from jobs need `OUTPUT_ROOTS`
because workers have no client MCP roots.

See [Integrations](index.md), [OpenCode](opencode.md),
[Getting Started](../getting-started.md), [Tools](../tools.md), and
[Configuration](../configuration.md).

## Official Sources

- [FastMCP client transports and subprocess lifecycle](https://gofastmcp.com/clients/transports)
- [FastMCP tool calls and structured results](https://gofastmcp.com/clients/tools)

Sources consulted 2026-11-10, with Context7 corroboration for
`StdioTransport`. The constructor and client APIs were checked against this
checkout's locked FastMCP runtime; provider generation is not part of setup
validation.
