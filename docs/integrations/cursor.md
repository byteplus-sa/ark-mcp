---
title: Cursor
description: Add Ark to Cursor at user or project scope using an absolute stdio launch and private env file.
---

This is a Cursor stdio connection template. Check your installed edition's
MCP settings and official configuration docs; it is not a guarantee that
every latest release has been tested end-to-end.

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

## Verify

Open Cursor's MCP settings, enable `ark-seed`, and review server/tool
approval prompts. Check active jobs before reloading its connection or
restarting Cursor; UI labels vary by edition.

Ask a tool-enabled agent to call `ark_list_models` with `{"input": {}}`
and `ark_job_capabilities` with `{}` only. Confirm actual output and
expected enabled services/model limits/targets. These calls make no provider
calls and do not prove key validity or entitlement. Do not generate,
understand, transcribe, upload, or submit jobs for verification.

Use ordinary `ark_job_submit` and `ark_job_get` by default for long work.
Native MCP task augmentation is not assumed. Jobs cannot be polled from
another client's stdio process, and the default queue is lost on restart.

See [Integrations](index.md), [VS Code](vscode.md),
[Getting Started](../getting-started.md), [Tools](../tools.md), and
[Configuration](../configuration.md).

## Official Source

- [Cursor MCP configuration and scope](https://cursor.com/docs/context/mcp)

Source consulted 2026-11-10. Runtime verification in the target Cursor
edition remains part of installation, not a promise made by this template.
