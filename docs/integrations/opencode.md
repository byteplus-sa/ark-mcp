---
title: OpenCode
description: Connect Ark to OpenCode using the MCP schema supported by your edition and a private env-file path.
---

OpenCode starts Ark from its saved MCP configuration. Let its coding agent
choose the format supported by the version you have installed.

## Recommended Setup

1. Send the [guided setup request](../setup/install.md#guided-local-setup)
   to an OpenCode session with terminal and file-editing access.
2. Approve the setup changes and enter keys in the private editor.
3. Review the `ark-seed` connection and tool permissions in OpenCode.

The classic and V2 configuration shapes below are alternatives for manual
setup. You do not need to choose or edit JSON yourself when the setup agent
can verify your installed schema.

<details>
<summary>Manual configuration for classic and V2 editions</summary>

## Choose the Scope

- **User/global default:** `~/.config/opencode/opencode.json` or
  `opencode.jsonc`, subject to the selected profile/configuration overrides.
- **Project:** project-root `opencode.json` or `opencode.jsonc`. V2 docs also
  describe `.opencode/opencode.json(c)` and hierarchical merging.

Complete the [shared prerequisites](index.md#prepare-the-launch). Preserve
existing entries and JSONC comments. Check precedence and same-name entries:
V2 project overrides replace a whole server object, so partial overrides
can drop required fields. Never automatically commit machine-specific paths.

## Classic Repository Template

Use this only when your installed OpenCode schema puts server names directly
under `mcp`. `command` is an array, and environment settings use
`environment`, not `env`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "ark-seed": {
      "type": "local",
      "command": [
        "/absolute/path/to/uv",
        "--directory",
        "/absolute/path/to/ark-mcp",
        "run",
        "--locked",
        "--no-sync",
        "python",
        "-m",
        "ark_mcp"
      ],
      "enabled": true,
      "environment": {
        "ARK_MCP_ENV_FILE": "/absolute/path/to/private/ark-mcp/.env",
        "MCP_TRANSPORT": "stdio",
        "MCP_AUTH_MODE": "local"
      }
    }
  }
}
```

## V2 Official-Docs Variant

Use this only when the installed schema requires `mcp.servers` and uses
`disabled` rather than classic `enabled`. Here `disabled: false` permits
connection, and `codemode: false` requests directly exposed tools:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "ark-seed": {
        "type": "local",
        "command": [
          "/absolute/path/to/uv",
          "--directory",
          "/absolute/path/to/ark-mcp",
          "run",
          "--locked",
          "--no-sync",
          "python",
          "-m",
          "ark_mcp"
        ],
        "disabled": false,
        "codemode": false,
        "environment": {
          "ARK_MCP_ENV_FILE": "/absolute/path/to/private/ark-mcp/.env",
          "MCP_TRANSPORT": "stdio",
          "MCP_AUTH_MODE": "local"
        }
      }
    }
  }
}
```

Replace all absolute-path placeholders on the actual execution host.
`type: "local"` means subprocess transport, not project scope. Both variants
contain only non-secret environment settings; Ark reads credentials from the
private file. OpenCode may inherit other environment variables that override
that file, so check presence/names without printing values.

</details>

## Verify

Run the [discovery-only check](index.md#verify-without-paid-calls), then
[Your First Tool Call](../setup/first-run.md). Finish active jobs before
reloading the connection. If you need troubleshooting commands, ask the
agent to use the help for your installed OpenCode version.

See [Integrations](index.md), [Python](python.md),
[Getting Started](../getting-started.md), [Tools](../tools.md), and
[Configuration](../configuration.md).

## Official Sources

- [OpenCode MCP servers](https://opencode.ai/docs/mcp-servers/)
- [OpenCode configuration and precedence](https://opencode.ai/docs/config/)

Sources consulted 2026-11-10 describe V2. The classic shape follows this
repository's integration/setup references; check the installed edition
before selecting either template.
