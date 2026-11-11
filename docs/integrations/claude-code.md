---
title: Claude Code
description: Choose user, private-project, or shared-project scope for Ark in Claude Code.
---

This is a Claude Code connection template, not a [Claude Desktop](claude-desktop.md)
configuration or a guarantee for every latest CLI edition.

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

## Verify

After checking active jobs, reload the server/session and open `/mcp`.
Review project-server approval prompts when using `.mcp.json`.

Ask Claude Code to call `ark_list_models` with `{"input": {}}` and
`ark_job_capabilities` with `{}` only. Verify actual enabled services and
job targets, not just a registered entry. Both calls make no provider calls;
they do not establish API-key validity or account entitlement. Do not use
generation, understanding, transcription, or upload for this test.

Ordinary `ark_job_*` tools are the default for long operations. Native task
augmentation requires confirmed client support. Keep submission and polling
on the same server process; a restart loses the default in-memory jobs.
Treat CLI `list`/`get` output as potentially sensitive when unrelated servers
have inline credentials.

See [Integrations](index.md), [Getting Started](../getting-started.md),
[Tools](../tools.md), and [Configuration](../configuration.md).

## Official Source

- [Claude Code MCP connections and scopes](https://code.claude.com/docs/en/mcp)

Source consulted 2026-11-10. Check local help and the installed edition
before registration.
