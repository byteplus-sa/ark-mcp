---
title: Claude Code
description: Choose user, private-project, or shared-project scope for Ark in Claude Code.
---

Use Claude Code's terminal-enabled agent to prepare Ark for you. This guide is
for the coding client, not [Claude Desktop](claude-desktop.md).

## Recommended Setup

1. Send the [guided setup request](../setup/install.md#guided-local-setup)
   to Claude Code, naming **Claude Code** as the target.
2. Approve setup changes and enter provider keys in the private editor.
3. Open `/mcp`, review the connection and enable `ark-seed`.

Claude Code starts the server automatically. You do not need to run the
registration command below yourself if your setup agent has completed it.

<details>
<summary>Manual configuration and scope options</summary>

## Choose the Scope

| CLI scope | Visibility | Storage |
| --- | --- | --- |
| `user` | All your projects; recommended default | User configuration, normally `~/.claude.json` |
| `local` | Private to the current project | Project-specific entry in user configuration |
| `project` | Shareable configuration for this project | Project-root `.mcp.json` |

Claude Code's default `local` scope is **not global**. `CLAUDE_CONFIG_DIR`
can change user configuration locations; prefer the CLI over editing that
file manually. For either project scope, run registration from the intended
project, which need not be the Ark checkout.

## Register

Complete the [shared prerequisites](index.md#prepare-the-launch). Check
`claude mcp add --help` for the installed CLI before using these Bash/POSIX
commands. The variables contain only absolute paths, never credential values:

```bash
UV="/absolute/path/to/uv"
CHECKOUT="/absolute/path/to/ark-mcp"
ENV_FILE="/absolute/path/to/private/ark-mcp/.env"

claude mcp add ark-seed --transport stdio --scope user \
  --env "ARK_MCP_ENV_FILE=$ENV_FILE" \
  --env MCP_TRANSPORT=stdio \
  --env MCP_AUTH_MODE=local \
  -- "$UV" --directory "$CHECKOUT" run --locked --no-sync python -m ark_mcp
```

Put the server name before `--env`, since that option may consume multiple
values. `--` separates Claude's options from the server command.
Replace `--scope user` with `--scope local` for a private project entry, or
`--scope project` for the shareable file. Do not blindly re-add or remove an
existing `ark-seed` entry to resolve shadowing.

For manual project configuration, merge this entry into `.mcp.json`:

```json
{
  "mcpServers": {
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

These machine-specific paths are not portable team defaults. Each teammate
needs their own private env file and installation. Do not commit populated
credential files or automatically commit this configuration.

</details>

## Verify

Use the [discovery-only check](index.md#verify-without-paid-calls), then try
[Your First Tool Call](../setup/first-run.md). Confirm actual tool results,
not just an entry in `/mcp`. Finish active jobs before restarting the connection.

See [Integrations](index.md), [Getting Started](../getting-started.md),
[Tools](../tools.md), and [Configuration](../configuration.md).

## Official Source

- [Claude Code MCP connections and scopes](https://code.claude.com/docs/en/mcp)

Source consulted 2026-11-10. Check local help and the installed edition
before registration.
