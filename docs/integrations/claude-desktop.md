---
title: Claude Desktop
description: Register a local Ark stdio server in Claude Desktop without putting keys in client configuration.
---

This guide is for **Claude Desktop**, not [Claude Code](claude-code.md).
The configuration below is a connection template; verify the settings UI
and local MCP support in your installed Desktop edition.

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

## Verify

Check active jobs before fully quitting and reopening Desktop. Review any
server trust prompt, then locate `ark-seed` in the app's tools/connectors UI;
labels and icons vary by edition.

Ask Desktop to call `ark_list_models` with `{"input": {}}` and
`ark_job_capabilities` with `{}` only. Confirm actual tool results, enabled
services, and the targets you expected. Do not generate, upload, or run
understanding as a connection test. These discovery calls make no provider
calls and do not verify provider credentials or entitlement.

For long operations, instruct Desktop to use ordinary `ark_job_submit`
and `ark_job_get` calls by default. Native task support is not assumed.
Restarting its stdio process loses the default in-memory job queue.

See [Integrations](index.md), [Getting Started](../getting-started.md),
[Tools](../tools.md), and [Configuration](../configuration.md).

## Official Source

- [MCP: Connect to local servers, including Desktop configuration](https://modelcontextprotocol.io/docs/develop/connect-local-servers)

Source consulted 2026-11-10. The JSON is not a claim of end-to-end
verification in every latest Desktop edition.
