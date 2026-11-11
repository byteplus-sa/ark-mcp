---
title: Cursor
description: Add Ark to Cursor at user or project scope using an absolute stdio launch and private env file.
---

Cursor starts Ark automatically from its saved MCP configuration.

## Recommended Setup

1. Use the [guided setup request](../setup/install.md#guided-local-setup)
   in a Cursor agent with terminal and file-editing access.
2. Approve installation/configuration changes and enter provider keys privately.
3. Open Cursor's MCP settings, enable `ark-seed`, and review tool permissions.

Use user-level setup unless only one project should see Ark. The agent should
prepare the correct entry without replacing your other MCP servers.

<details>
<summary>Manual configuration and scope options</summary>

## Choose the Scope

- **User/global default:** `~/.cursor/mcp.json`.
- **Project:** `<project>/.cursor/mcp.json`.

Both use `mcpServers`. Merge only the intended `ark-seed` entry and preserve
other entries/comments. Check for another same-name server at a different
scope instead of deleting it. Machine-specific project configuration and
private credentials should not be committed automatically.

## Configure

Complete the [shared prerequisites](index.md#prepare-the-launch), then use
this JSON in the chosen file. Replace the three placeholders with actual
absolute paths on the server host:

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

The private `.env` is read by Ark, not embedded in Cursor configuration.
Although some Cursor editions support `envFile` and path interpolation,
this template deliberately uses literal absolute paths and only non-secret
`env` settings. Remote workspaces need paths and an installation on the
actual execution host.

</details>

## Verify

Run the [discovery-only check](index.md#verify-without-paid-calls), then
[Your First Tool Call](../setup/first-run.md). UI labels vary by edition;
actual tool results matter more than the connection indicator. Finish active
jobs before reconnecting or restarting Cursor.

See [Integrations](index.md), [VS Code](vscode.md),
[Getting Started](../getting-started.md), [Tools](../tools.md), and
[Configuration](../configuration.md).

## Official Source

- [Cursor MCP configuration and scope](https://cursor.com/docs/context/mcp)

Source consulted 2026-11-10. Runtime verification in the target Cursor
edition remains part of installation, not a promise made by this template.
