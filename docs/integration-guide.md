# Integration Guide

How to connect the Ark Seed MCP server to popular MCP clients.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Python 3.12+
- BytePlus account with ModelArk API key and/or Seed Audio API key
- This repository cloned locally

## Server Overview

The server runs as a `stdio` process — the MCP client spawns it as a
subprocess and communicates over stdin/stdout. The entry point is
`python -m ark_mcp`, which injects `truststore` for macOS TLS
certificate verification before starting the FastMCP server.

Use `python -m ark_mcp` in client configurations so transport security
settings are applied consistently. The server module also injects `truststore`
before provider clients are created.

## Background Execution

**Long-running work stays asynchronous for every client.** The server exposes
two entry paths to the same FastMCP Docket worker:

| Entry path | Client requirement | Result retrieval |
|---|---|---|
| `ark_job_submit` with the original tool name and arguments (**default**) | Ordinary MCP tools only | `ark_job_get` |
| Call the original tool with task augmentation | MCP `2026-07-28` task extension | `tasks/get` |

**Use the ordinary `ark_job_*` tools by default.** MCP tasks remain
experimental and most clients — including the current Codex, Cursor, Claude
Desktop, and VS Code transports — cannot negotiate task augmentation. The
ordinary path needs no task-specific transport feature, runs the selected tool
in the same background worker, and preserves the original tool result inside
`result.structured_content` when the job completes.

A client that does negotiate the task extension may call the original tools
with task metadata instead; that path is fully supported and unchanged. A
foreground call made directly to a required-task tool is still rejected before
provider submission.

The repository tests both paths with the locked FastMCP **4.0.x** runtime. The
ordinary path is covered in-process, over a real subprocess stdio transport, and
over authenticated Streamable HTTP without advertising the task extension.
These tests use mocked providers and do not make billable generation calls.
Exact desktop/IDE releases and their automatic tool-selection behavior can
change, so the connection snippets below are templates; the standard
`ark_job_*` protocol surface is the portable contract.

### Ordinary-tool workflow (default)

1. Call `ark_job_capabilities` to discover targets enabled by server
   configuration and visible to the current principal.
2. Call `ark_job_submit` with `tool_name` and the exact `arguments` object the
   original tool accepts.
3. Store the returned Ark `job_id`. Do not confuse it with a Seedance, Seed 3D,
   or VOD provider task ID returned later by the original tool.
4. Poll `ark_job_get` no faster than `poll_after_ms` until `status` is terminal.
5. Read the original MCP tool result from `result`. For provider-submission
   tools, its `structured_content` contains the provider task ID.
6. Use `ark_job_cancel` for a locally running job when needed. Cancellation is
   cooperative and does not guarantee cancellation of provider work already
   accepted upstream.

```python
import asyncio

from fastmcp import Client


async def run_without_task_extension(server):
    async with Client(server, mode="legacy") as client:
        submitted = await client.call_tool(
            "ark_job_submit",
            {
                "input": {
                    "tool_name": "seed_understand",
                    "arguments": {
                        "input": {
                            "prompt": "Analyze this video",
                            "videos": [
                                {
                                    "kind": "url",
                                    "url": "https://example.com/video.mp4",
                                }
                            ],
                        }
                    },
                }
            },
        )
        job_id = submitted.structured_content["job_id"]
        while True:
            snapshot = await client.call_tool(
                "ark_job_get", {"input": {"job_id": job_id}}
            )
            state = snapshot.structured_content
            if state["status"] != "working":
                return state["result"]
            await asyncio.sleep(state["poll_after_ms"] / 1000)
```

### Native task workflow

A client that negotiates the task extension can call the same tools with task
augmentation instead; that path follows the official
[FastMCP task documentation](https://gofastmcp.com/servers/tasks). The example
under [Python Task Workflow](#python-task-workflow) uses it.

## Python Task Workflow

Run this from the repository using `uv run python`, with credentials configured
in `.env` and `SMOKE_REFERENCE_IMAGE_URL` pointing to an accessible reference
image. **This example submits one billable video generation.** The provider ID
is saved before polling; if polling fails, use that ID to resume retrieval rather
than repeating submission. The MCP task ID identifies the worker operation,
while the provider ID identifies the video generation.

```python
import asyncio
import json
import os
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from fastmcp_tasks import call_tool_task


async def main():
    transport = StdioTransport(
        command=sys.executable, args=["-m", "ark_mcp"]
    )
    async with Client(transport) as client:
        submission = await call_tool_task(
            client,
            "seedance_create_task",
            {"input": {
                "prompt": "A gentle camera move through the scene",
                "images": [{
                    "kind": "url",
                    "url": os.environ["SMOKE_REFERENCE_IMAGE_URL"],
                    "role": "reference_image",
                }],
                "duration": 5,
                "resolution": "480p",
            }},
        )
        print("MCP submission task:", submission.task_id)
        created = await submission.result()
        provider_id = created.structured_content["task_id"]
        directory = Path(".artifacts")
        directory.mkdir(exist_ok=True)
        with (directory / "provider_tasks.jsonl").open("a") as stream:
            stream.write(json.dumps({"task_id": provider_id}) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

        for _ in range(30):
            status = await client.call_tool(
                "seedance_get_task",
                {"input": {"task_id": provider_id, "persist_output": False}},
            )
            state = status.structured_content["status"]
            if state == "succeeded":
                break
            if state in {"failed", "cancelled", "expired"}:
                raise RuntimeError(f"Provider task {provider_id}: {state}")
            await asyncio.sleep(10)
        else:
            raise TimeoutError(f"Resume polling provider task {provider_id}")

        persistence = await call_tool_task(
            client,
            "seedance_get_task",
            {"input": {"task_id": provider_id, "persist_output": True}},
        )
        completed = await persistence.result()
        print(completed.structured_content["video"])


asyncio.run(main())
```

`task.result()` waits through `tasks/get` and returns the terminal tool result.
In this single-process stdio example, FastMCP 4.0.3 automatically task-augments
`client.call_tool()` for the optional status tool as well; `persist_output=false`
still prevents downloads. The standalone smoke scripts use modern and public
`mode="legacy"` clients connected to the same in-process server lifespan to
exercise a genuinely foreground status call. Do not launch a second stdio server
against the same SQLite state to obtain that behavior.
A provider success can still have `video=null` when best-effort persistence
fails; inspect the result before treating the artifact as delivered. The same
submit/status/persistence sequence is tested offline by the smoke-workflow tests;
`tests/e2e/test_stdio_tasks.py` separately verifies a real subprocess task result.

## Providing Credentials

Two options:

1. **`.env` file** at the repository root (recommended for local dev):
   ```bash
   cp .env.example .env
   # Edit .env and fill in your API keys
   ```

2. **Inline in the client config** (recommended for shared configs):
   Pass the keys in the `env` block of the client's MCP server configuration.

The `.env` approach is simplest because the server automatically loads it.
Inline env vars take precedence.

## Claude Desktop

Add the server to your `claude_desktop_config.json`:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "ark-seed": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/ark-mcp",
        "run",
        "python",
        "-m",
        "ark_mcp"
      ],
      "env": {
        "BYTEPLUS_MODELARK_API_KEY": "your_ark_api_key"  # pragma: allowlist secret,
        "BYTEPLUS_SEED_SPEECH_API_KEY": "your_seed_speech_key"  # pragma: allowlist secret
      }
    }
  }
}
```

Replace `/path/to/ark-mcp` with the absolute path to your repository.

Fully quit and restart Claude Desktop. The tools appear under the hammer
icon. If you use the `.env` file approach, you can omit the `env` block.

## Cursor IDE

Create `.cursor/mcp.json` in your project root (or
`~/.cursor/mcp.json` for global config):

```json
{
  "mcpServers": {
    "ark-seed": {
      "type": "stdio",
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/ark-mcp",
        "run",
        "python",
        "-m",
        "ark_mcp"
      ],
      "env": {
        "BYTEPLUS_MODELARK_API_KEY": "your_ark_api_key"  # pragma: allowlist secret,
        "BYTEPLUS_SEED_SPEECH_API_KEY": "your_seed_speech_key"  # pragma: allowlist secret
      }
    }
  }
}
```

Alternatively, use `${env:VAR}` interpolation to reference shell
environment variables:

```json
{
  "mcpServers": {
    "ark-seed": {
      "type": "stdio",
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/ark-mcp",
        "run",
        "python",
        "-m",
        "ark_mcp"
      ],
      "env": {
        "BYTEPLUS_MODELARK_API_KEY": "${env:BYTEPLUS_MODELARK_API_KEY}",
        "BYTEPLUS_SEED_SPEECH_API_KEY": "${env:BYTEPLUS_SEED_SPEECH_API_KEY}"
      }
    }
  }
}
```

Fully quit and restart Cursor. A green dot in Settings → Features → MCP
confirms the connection.

## VS Code (MCP Extension)

Create `.vscode/mcp.json` in your workspace root:

```json
{
  "servers": {
    "ark-seed": {
      "type": "stdio",
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/ark-mcp",
        "run",
        "python",
        "-m",
        "ark_mcp"
      ],
      "envFile": "${workspaceFolder}/.env"
    },
    "inputs": []
  }
}
```

VS Code uses `"servers"` (not `"mcpServers"`). The `envFile` field loads
the repo's `.env` directly — no need to inline keys.

Run the **MCP: List Servers** command to verify the server is running.
Start/stop/restart controls appear as code lenses.

## MCP Inspector

The MCP Inspector provides a web UI for testing tools interactively.

**Option 1: Makefile (easiest)**

```bash
make inspect
```

This launches `fastmcp inspect src/ark_mcp/server.py:mcp`; the server
module injects `truststore` before provider clients are created.

**Option 2: npx inspector (recommended for macOS)**

```bash
export BYTEPLUS_MODELARK_API_KEY=your_key  # pragma: allowlist secret
export BYTEPLUS_SEED_SPEECH_API_KEY=your_key  # pragma: allowlist secret

npx @modelcontextprotocol/inspector \
  uv --directory /path/to/ark-mcp run python -m ark_mcp
```

The browser UI opens—select **STDIO** transport and click **Connect**.

**Option 3: HTTP transport**

Start the server in HTTP mode:

```bash
make start-http
```

Then connect the Inspector to `http://127.0.0.1:3000/mcp`.

## Troubleshooting

### SSL Certificate Errors (macOS)

If you see `CERTIFICATE_VERIFY_FAILED`, confirm `truststore` is installed and
start through `python -m ark_mcp` so the complete entrypoint runs.

### "API key not configured" Error

The server skips registering tools for products without credentials. Check:

1. `.env` file exists at the repository root with the correct key names
2. Keys are spelled exactly: `BYTEPLUS_MODELARK_API_KEY`,
   `BYTEPLUS_SEED_SPEECH_API_KEY`
3. No leading/trailing whitespace in values
4. Run `make check-env` to validate

### Server Not Discovered by Client

1. Verify `uv` is on the client's PATH (or use a full path:
   `/Users/yourname/.local/bin/uv`)
2. Verify the `--directory` path is correct and absolute
3. Check the client's logs for stderr output from the server
4. Test manually: `uv --directory /path/to/ark-mcp run python -m ark_mcp`
   (should start and wait for stdin)

### Tools Missing

If only some products' tools are registered:

- No ModelArk key → no Seedream or Seedance tools (5 tools missing)
- No Seed Audio key → no Seed Audio tools (2 tools missing)

Both keys are required for all 9 tools to appear.

## Environment Variables Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `BYTEPLUS_MODELARK_API_KEY` | For image/video | — | ModelArk Bearer auth key |
| `BYTEPLUS_SEED_SPEECH_API_KEY` | For audio | — | Seed Speech X-Api-Key |
| `BYTEPLUS_MODELARK_BASE_URL` | No | `https://ark.ap-southeast.bytepluses.com/api/v3` | ModelArk region endpoint |
| `BYTEPLUS_SEED_AUDIO_BASE_URL` | No | `https://voice.ap-southeast-1.bytepluses.com` | Seed Speech endpoint |
| `SEEDREAM_DEFAULT_MODEL` | No | `dola-seedream-5-0-pro-260628` | Seedream model ID |
| `SEEDANCE_DEFAULT_MODEL` | No | `dreamina-seedance-2-0-260128` | Seedance model ID |
| `MCP_TRANSPORT` | No | `stdio` | `stdio` or `http` |
| `MCP_HOST` | No | `127.0.0.1` | HTTP bind address |
| `MCP_PORT` | No | `3000` | HTTP port |
| `MCP_AUTH_MODE` | No | `local` | Set `jwt` for non-loopback HTTP |
| `MCP_JWT_JWKS_URI` | For JWT | — | HTTPS JSON Web Key Set URL |
| `MCP_JWT_ISSUER` | For JWT | — | Required token issuer |
| `MCP_JWT_AUDIENCE` | For JWT | — | Required token audience |
| `MCP_TENANT_CLAIM` | No | `tenant_id` | Tenant-isolation claim |
| `MCP_ALLOWED_HOSTS` | For HTTP | loopback | Accepted Host headers |
| `MCP_ALLOWED_ORIGINS` | For browser HTTP | — | Accepted browser origins |
| `ARTIFACT_DIR` | No | `.artifacts` | Local artifact storage |
| `ARTIFACT_TTL_SECONDS` | No | `604800` (7 days) | Artifact retention |

See [Configuration](configuration.md) for model bindings, body limits,
concurrency, and budget settings.
