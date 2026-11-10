---
title: VS Code
description: Select the correct VS Code MCP format and scope for a private local Ark connection.
---

Use VS Code's MCP support for the selected chat harness/profile. These are
connection templates, not universally verified latest-edition configurations.
Newer official documentation distinguishes VS Code's compatibility format
from portable Copilot configuration; do not treat them as interchangeable.

## Choose Scope and Format

| Destination | Scope | Envelope |
| --- | --- | --- |
| `.vscode/mcp.json` | Workspace, VS Code compatibility format | `servers` |
| Selected user profile's `mcp.json` | User/profile, compatibility format | `servers` |
| Workspace-root `.mcp.json`, where supported | Project, portable Copilot format | `mcpServers` |
| Copilot user configuration | User/global for that harness | `mcpServers` |

For user/global setup, use **MCP: Add Server** and select the supported
user/profile or harness destination. Do not guess the profile's storage path.
Current official docs place portable Copilot user configuration at
`$COPILOT_HOME/mcp-config.json`, falling back to `~/.copilot/mcp-config.json`;
confirm this in your installed edition. Older editions may offer only the
VS Code profile/workspace format.

Complete the [shared prerequisites](index.md#prepare-the-launch). A remote
or Dev Container session needs the installation and paths on its execution
host, not automatically on the local desktop.

## VS Code Format

Merge this into workspace `.vscode/mcp.json`, or the profile file opened by
the supported configuration command. Use `servers`, not `mcpServers`:

```json
{
  "servers": {
    "ark-seed": {
      "type": "stdio",
      "command": "/absolute/path/to/uv",
      "args": [
        "--directory",
        "/absolute/path/to/ark-mcp",
        "run",
        "--locked",
        "--no-sync",
        "python",
        "-m",
        "ark_mcp"
      ],
      "env": {
        "ARK_MCP_ENV_FILE": "/absolute/path/to/private/ark-mcp/.env",
        "MCP_TRANSPORT": "stdio",
        "MCP_AUTH_MODE": "local"
      }
    }
  }
}
```

Replace all placeholder paths. Optional `inputs` belongs at the top level,
not inside `servers`; this example needs no interactive credential inputs.
Do not use `envFile` to distribute provider keys. Ark reads its private file
through the non-secret `ARK_MCP_ENV_FILE` path.

## Portable Copilot Format

Only use this variant when your installed harness supports the corresponding
portable destination. Merge it into that destination, not `.vscode/mcp.json`:

```json
{
  "mcpServers": {
    "ark-seed": {
      "command": "/absolute/path/to/uv",
      "args": [
        "--directory",
        "/absolute/path/to/ark-mcp",
        "run",
        "--locked",
        "--no-sync",
        "python",
        "-m",
        "ark_mcp"
      ],
      "env": {
        "ARK_MCP_ENV_FILE": "/absolute/path/to/private/ark-mcp/.env",
        "MCP_TRANSPORT": "stdio",
        "MCP_AUTH_MODE": "local"
      }
    }
  }
}
```

Portable configuration does not need `envFile` or VS Code `${input:...}`
support because the server loads its private credentials itself.
Preserve existing entries, avoid duplicate same-name definitions, and do
not automatically commit machine-specific project configuration.

## Verify

Check active jobs before using start/restart controls. Use **MCP: List
Servers** where available, or **Chat: Open Customizations > MCP Servers**
in newer editions. Review trust prompts and enable tools in the chosen
agent/harness. A registered server alone is not a verified tool connection.

Request `ark_list_models` with `{"input": {}}` and
`ark_job_capabilities` with `{}` only. Confirm actual results and expected
services/targets without generation, understanding, transcription, uploads,
or job submission. Both discovery calls make no provider calls; they do
not establish key validity or provider entitlement.

Ordinary `ark_job_*` tools are the default for long operations; do not assume
native task augmentation. Poll the same process that accepted the job.
Restart loses the default in-memory queue.

See [Integrations](index.md), [Cursor](cursor.md),
[Getting Started](../getting-started.md), [Tools](../tools.md), and
[Configuration](../configuration.md).

## Official Sources

- [Add MCP servers in VS Code](https://code.visualstudio.com/docs/copilot/customization/mcp-servers)
- [VS Code MCP configuration formats](https://code.visualstudio.com/docs/copilot/reference/mcp-configuration)

Sources consulted 2026-11-10. The compatibility and portable variants still
require verification against the actual installed edition and harness.
