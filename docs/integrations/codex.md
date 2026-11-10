---
title: Codex
description: Connect Codex to Ark using global or trusted-project MCP configuration and private env-file paths.
---

These are Codex connection templates, not a guarantee for every latest CLI,
IDE extension, or app edition. Check the installed client's MCP settings and
CLI help before registration.

## Choose the Scope

- **User/global:** `${CODEX_HOME}/config.toml`, normally `~/.codex/config.toml`.
- **Project:** `<project>/.codex/config.toml`, loaded only for trusted projects.

`codex mcp add` writes user/global configuration in the repository's documented
workflow; it is not the project-only registration command. Do not change
project trust automatically or delete a competing same-name entry. Clients
sharing the same Codex configuration do not need duplicate registrations.

Complete the [shared prerequisites](index.md#prepare-the-launch). Registration
scope does not change where Ark's checkout or private credentials live.

## Global CLI Registration

Check `codex mcp add --help`. In a Bash/POSIX shell:

```bash
UV="/absolute/path/to/uv"
CHECKOUT="/absolute/path/to/ark-mcp"
ENV_FILE="/absolute/path/to/private/ark-mcp/.env"

codex mcp add ark-seed \
  --env "ARK_MCP_ENV_FILE=$ENV_FILE" \
  --env MCP_TRANSPORT=stdio \
  --env MCP_AUTH_MODE=local \
  -- "$UV" --directory "$CHECKOUT" run --locked --no-sync python -m ark_mcp
```

All variable values are non-secret absolute paths. Use host-native quoting
on Windows rather than pasting Bash continuations into PowerShell.

## TOML Configuration

For project-only setup, merge this into the trusted project's
`.codex/config.toml`. The same table can instead be placed in the user/global
file; choose one intended scope:

```toml
[mcp_servers.ark-seed]
command = "/absolute/path/to/uv"
args = [
  "--directory",
  "/absolute/path/to/ark-mcp",
  "run",
  "--locked",
  "--no-sync",
  "python",
  "-m",
  "ark_mcp",
]

[mcp_servers.ark-seed.env]
ARK_MCP_ENV_FILE = "/absolute/path/to/private/ark-mcp/.env"
MCP_TRANSPORT = "stdio"
MCP_AUTH_MODE = "local"
```

Preserve unrelated tables and comments. Never put provider keys in `env`.
Project configuration with machine-specific absolute paths is not a portable
team setup and should not be committed automatically.

## Verify

Check active jobs before reloading Codex's MCP connection. Use `/mcp` where
available, or the client's MCP/tool-discovery panel.

Request `ark_list_models` with `{"input": {}}` and
`ark_job_capabilities` with `{}` only. Confirm returned enabled services,
exact model IDs and limits, and available targets. These read-only calls make
no provider calls and prove configuration loading, not key validity or
entitlement. Do not generate, understand, transcribe, upload, or submit a
job to verify connection.

Ordinary `ark_job_*` calls are the default for long work; native MCP tasks
require explicit client support. Jobs belong to the process that accepted
them, not to every client using the same configuration. The default queue
loses retained jobs when that process restarts.

See [Integrations](index.md), [Claude Code](claude-code.md),
[Getting Started](../getting-started.md), [Tools](../tools.md), and
[Configuration](../configuration.md).

## Official Sources

- [Codex MCP configuration](https://developers.openai.com/codex/mcp/)
- [Codex configuration reference](https://developers.openai.com/codex/config-reference/)

Sources consulted for MCP setup on 2026-11-10. CLI/IDE/app support must still
be checked in the actual target client.
