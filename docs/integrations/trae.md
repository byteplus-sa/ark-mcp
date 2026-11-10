---
title: TRAE
description: Configure Ark in TRAE IDE or Work with explicit scope, local runtime paths, and private credentials.
---

Identify **TRAE IDE versus TRAE Work** and **International versus CN** before
configuring MCP. UI labels, scope support, and runtime choices vary. This is
a repository-aligned connection template, not universal verification of the
latest editions.

## Scope and Runtime

- **User/global default:** use the selected edition's MCP settings UI.
  In IDE, look for **Settings > MCP > Add > Manual configuration**.
  In Work, look for **Settings > MCP > Create > Manual configuration**.
  Confirm the selected scope in the actual UI.
- **Project:** the repository's IDE template uses `<project>/.trae/mcp.json`
  with project-level MCP enabled in settings. Verify support in your edition.
  Do not claim global setup by writing a project file.
- **Execution host:** select local runtime for this guide. Work editions may
  offer local/cloud selection. Cloud requires a checkout, `uv`, and private
  credentials accessible on that cloud host; local paths will not work.
  Do not transfer credentials to another host without explicit approval.

Do not guess a global `~/.trae/mcp.json` path from the existence of that
directory. If the edition only offers a UI for user-level setup, use the UI.

## Configure

Complete the [shared prerequisites](index.md#prepare-the-launch). Paste this
JSON into the supported manual configuration UI, or merge the entry into
the supported project file. Replace each placeholder with an absolute
path on the selected server host:

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

The `env` contains no key values. The Ark checkout does not need to be the
project being edited; do not substitute `${workspaceFolder}` for its path
unless that folder actually contains the prepared server checkout.
Preserve existing servers and any same-name entry at a different scope.

## Verify

Check active jobs before reloading the server. Enable `ark-seed` in the
edition's MCP/tool settings and select a chat/agent mode that can use it.

Request `ark_list_models` with `{"input": {}}` and
`ark_job_capabilities` with `{}` only. Confirm actual tool results and
expected services/targets. Both calls make no provider calls; successful
discovery does not verify provider credentials or whitelist access.
Do not generate, understand, transcribe, upload, or submit jobs as a setup test.

Use ordinary `ark_job_*` calls by default for long operations. Do not infer
native MCP task support from the MCP connection indicator. Submit and poll
through the same stdio process; restarting it loses the default in-memory jobs.
If UI registration cannot be completed, record it as pending rather than
calling setup verified.

See [Integrations](index.md), [Codex](codex.md) for an embedded Codex agent,
[Getting Started](../getting-started.md), [Tools](../tools.md), and
[Configuration](../configuration.md).

## Official Sources

- [TRAE International IDE MCP overview](https://docs.trae.ai/ide/model-context-protocol)
- [TRAE Work MCP and runtime guide](https://docs.trae.cn/work/remote-mcp-server)

The IDE overview was consulted on 2026-11-10. The Work page could not be
retrieved during this documentation pass; Work UI/runtime instructions
follow the repository's setup references and must be checked in the app.
