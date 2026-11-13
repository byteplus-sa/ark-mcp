---
title: Claude Desktop
description: Register a local Ark stdio server in Claude Desktop without putting keys in client configuration.
---

This guide is for **Claude Desktop**, not [Claude Code](claude-code.md).
Desktop launches the local Ark server automatically after setup.

## Recommended Setup

1. Use the [guided setup request](../setup/install.md#guided-local-setup)
   in a separate coding agent with local terminal and file-editing access.
   Name **Claude Desktop** as the target; Desktop chat alone is not assumed
   to have installation access.
2. Let it prepare your installation and Desktop configuration, and enter
   your provider keys in the private editor.
3. Finish active jobs, fully reopen Desktop, and approve the Ark connection.

No setup-capable agent available? Use [manual installation](../setup/manual-install.md)
or ask your administrator for help. For a team-hosted endpoint, follow the
[shared-server guide](../setup/shared-server.md) instead.

<details>
<summary>Manual Desktop configuration</summary>

## Scope and Prerequisites

Desktop's native MCP configuration is user-level, available across Desktop
conversations. This template has no project scope. For project-scoped CLI
work, use the [Claude Code guide](claude-code.md).

Complete the [shared launch prerequisites](index.md#prepare-the-launch):
a locked sync, an absolute `uv` path, an absolute checkout path, and a private
credential file outside the checkout. Do not add provider keys to Desktop's
configuration.

## Configure

Open Desktop's **Settings > Developer > Edit Config** to locate its actual
configuration. Common locations:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

Those are location shorthand, not literal launcher paths. Confirm support
and location through the installed app on other platforms.

Merge this `ark-seed` entry into the existing `mcpServers` object. Preserve
other servers and replace all placeholder paths with host-native absolute
paths:

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

The `env` values are non-secret. Desktop starts the server locally; this
does not register a hosted Claude connector.

</details>

## Verify

Locate `ark-seed` in Desktop's tools/connectors settings and run the
[discovery-only check](index.md#verify-without-paid-calls). UI labels vary
by edition. Once your expected tools appear, try
[Your First Tool Call](../setup/first-run.md).

See [Integrations](index.md), [Getting Started](../getting-started.md),
[Tools](../tools.md), and [Configuration](../configuration.md).

## Official Source

- [MCP: Connect to local servers, including Desktop configuration](https://modelcontextprotocol.io/docs/develop/connect-local-servers)

Source consulted 2026-11-10. The JSON is not a claim of end-to-end
verification in every latest Desktop edition.
