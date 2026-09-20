# Getting Started

This guide covers installing, configuring, and running the Ark Seed
Multimodal MCP Server.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (modern Python package manager)
- BytePlus account with Seed Audio, Seedream, and/or Seedance activated

## Installation

Clone the repository and install dependencies:

```bash
git clone <repo-url> ark-mcp
cd ark-mcp
cp .env.example .env
uv sync
```

## Configuration

Edit `.env` and fill in your BytePlus credentials:

```dotenv
BYTEPLUS_MODELARK_API_KEY=<your-modelark-key>
BYTEPLUS_SEED_SPEECH_API_KEY=<your-seed-audio-key>
```

ModelArk keys are region-scoped. Verify the base URL matches your region's
endpoint. The defaults point to the `ap-southeast` region.

If a credential is absent, the server skips registering that product's
tools. This means you can run with only Seedream enabled, for example.

## Running the Server

### stdio (default, for local MCP clients)

```bash
make start
# or
uv run python -m ark_mcp
```

### Streamable HTTP (loopback development)

```bash
make start-http
# or
MCP_TRANSPORT=http MCP_PORT=3000 uv run python -m ark_mcp
```

Non-loopback HTTP is fail-closed and requires JWT issuer, audience, JWKS,
Host, and Origin configuration. See [Transports](transports.md).

### Dev mode (with auto-reload)

```bash
make dev
```

Auto-reload watches `src/` and re-imports changed modules on every tool call.
Use this when iterating on server code.

> **Important — restart after editing source.** Without `make dev` (which
> runs `fastmcp run --reload`), the server is a long-lived Python process
> that **does not** hot-reload changed source files. After editing any file
> under `src/`, restart the server for the changes to take effect. Use the
> `seed-health://status` MCP resource to verify the running build stamp
> matches your expectations.

## Verifying Your Setup

Validate your environment configuration:

```bash
make check-env
# or
uv run python -c "from ark_mcp.config.env import validate; validate()"
```

This checks that required environment variables are present and
syntactically valid without making any billable provider calls.

## Using with MCP Clients

Long operations must run in the background. By default, call the ordinary
`ark_job_submit` and `ark_job_get` tools, which work on any MCP client. A client
supporting MCP `2026-07-28` task augmentation can instead call the original
tools and poll `tasks/get`; both paths use the same worker. The locked
FastMCP 4.0.x runtime is tested across both paths; desktop and Inspector
configurations below are connection templates because client behavior varies by
release. See [Background Execution](integration-guide.md#background-execution)
before submitting generation.

### Claude Desktop

Add the server to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ark-seed": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/ark-mcp", "python", "-m", "ark_mcp"],
      "env": {
        "BYTEPLUS_MODELARK_API_KEY": "<your-key>",
        "BYTEPLUS_SEED_SPEECH_API_KEY": "<your-key>"
      }
    }
  }
}
```

### MCP Inspector

Launch the FastMCP inspector to explore tools interactively:

```bash
make inspect
```

## Next Steps

- [Configuration](configuration.md) — full environment variable reference
- [Tools](tools.md) — tool schemas and usage examples
- [Transports](transports.md) — stdio vs HTTP deployment
