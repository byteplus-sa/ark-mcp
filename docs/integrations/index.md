---
title: Client Integrations
description: Connect local MCP clients to Ark with private credentials and non-billable discovery.
---

Connect one client to a local Ark MCP checkout over stdio. Each client starts
its own server process. These are configuration templates, not a claim that
every latest client edition, operating system, or automatic tool-selection
workflow has been verified.

## Choose a Client

| Guide | User/global scope | Project scope |
| --- | --- | --- |
| [Claude Desktop](claude-desktop.md) | Desktop configuration | No project scope in this template |
| [Claude Code](claude-code.md) | `--scope user` | Private `local` or shared `project` |
| [Codex](codex.md) | `~/.codex/config.toml` by default | Trusted project's `.codex/config.toml` |
| [TRAE](trae.md) | Selected edition's MCP settings UI | `.trae/mcp.json` where supported |
| [Cursor](cursor.md) | `~/.cursor/mcp.json` | `.cursor/mcp.json` |
| [VS Code](vscode.md) | Selected profile/harness configuration | `.vscode/mcp.json` or portable format |
| [OpenCode](opencode.md) | `~/.config/opencode/opencode.json` by default | Project `opencode.json` |
| [Python](python.md) | No client registration | Explicit subprocess per program |

Default to user/global registration unless only one project should see the
tools. Scope controls visibility, not credential or job isolation. Merge only
the `ark-seed` entry into an existing configuration, preserve other entries
and JSONC comments, and check for a same-name entry at another scope. Do not
replace an entire file or change project trust to make a template work.

## Prepare the Launch

Follow [Getting Started](../getting-started.md) for installation and release
selection. Prefer a dedicated, reviewed stable-release checkout; do not reset
or update a dirty development checkout during client setup.

Replace all three placeholders in each guide with actual absolute paths on
the machine that runs the server:

| Placeholder | Meaning |
| --- | --- |
| `/absolute/path/to/uv` | Resolved `uv` executable, not just its directory |
| `/absolute/path/to/ark-mcp` | Checkout containing `pyproject.toml` and `uv.lock` |
| `/absolute/path/to/private/ark-mcp/.env` | Private credential file outside the checkout |

On a POSIX shell, `command -v uv` helps locate the executable. GUI clients may
have a different `PATH`. Do not rely on `~`, `$HOME`, or `${workspaceFolder}`
expansion inside arbitrary JSON. Use host-native paths; Windows JSON needs
escaped backslashes or forward slashes. A local macOS path is not usable in
WSL, an SSH host, a container, or a cloud sandbox.

Install the locked dependencies once before using the launch templates:

```bash
"/absolute/path/to/uv" --directory "/absolute/path/to/ark-mcp" sync --locked --no-dev
```

Then each client launches the same command:

```text
/absolute/path/to/uv --directory /absolute/path/to/ark-mcp run --locked --no-sync python -m ark_mcp
```

`--no-sync` requires the prepared environment. If dependencies are missing,
repair the installation with a deliberate locked sync, not a different
launcher. `python -m ark_mcp` runs the entrypoint that applies the server's
TLS setup. The client environment contains only non-secret settings:

```json
{
  "ARK_MCP_ENV_FILE": "/absolute/path/to/private/ark-mcp/.env",
  "MCP_TRANSPORT": "stdio",
  "MCP_AUTH_MODE": "local"
}
```

`local` here is the server's authentication mode, not a client configuration
scope. These templates do not configure a public HTTP server.

## Keep Credentials Private

Prepare an owner-only, plain-text `.env` outside Git checkouts, shared
workspaces, and cloud-synced folders. On macOS/Linux, use a private directory
with mode `0700` and file mode `0600`, checking ownership, symlinks, and ACLs
before creating or repairing them. On Windows, use owner-restricted ACLs.
Preserve an existing file; do not overwrite it or display its contents.

Enter only the needed provider keys in a local, non-AI text editor. Do not
paste keys into chat, client JSON/TOML, CLI arguments, or screenshots. Never
`source` the file: Ark reads dotenv as data. Recheck permissions after the
editor saves it. Owner-only plaintext is not a secrets vault; processes
running as the same OS user can still read it.

ModelArk, Seed Speech, VOD MediaKit, and Vision AI use separate credentials.
Optional products also need their documented feature flags. Do not enable
whitelist-only models or destructive operations as a setup shortcut. See
[Configuration](../configuration.md) for field names, region matching, and
feature gates. An inherited environment value can override the file; inspect
variable names/presence without printing values when diagnosing shadowing.

## Verify Without Paid Calls

After saving the entry, review the client's server trust prompt and enable
`ark-seed`. Check active jobs before reloading or restarting a client.

Use this request in the client:

```text
Use only the ark-seed server. List its tools, call ark_list_models with
{"input": {}}, and call ark_job_capabilities with {}. Report enabled
services, exact model IDs, model limits, and available job targets.
Do not generate, understand, transcribe, upload, or submit any jobs.
```

`ark_list_models` and `ark_job_capabilities` are read-only and make no provider
calls. The former describes configured models and services; the latter returns
enabled job targets and their actual input schemas. A connected server with
empty models/targets may simply have no provider credentials loaded.
Discovery proves configuration and tool availability, not API-key validity,
account entitlement, or model whitelist access.

For a standalone preflight, isolate artifact/runtime state in a private
temporary directory and use filesystem artifacts with `FASTMCP_DOCKET_URL=memory://`.
Set `BYTEPLUS_MODELARK_PRICING_REFRESH_SECONDS=0` and
`READINESS_CHECK_PROVIDERS=false` for that process to avoid optional provider
probes and catalog refreshes. Do not persist these preflight overrides into
the normal client entry. The [Python guide](python.md) disables those optional
provider checks for its discovery example.

## Long-Running Work

Use ordinary `ark_job_*` calls by default. Native MCP task augmentation is an
alternative only when the installed client negotiates it; do not assume that
support from a connection indicator.

1. Discover models with `ark_list_models` and targets with `ark_job_capabilities`.
2. After approving the operation and possible charges, call `ark_job_submit`
   with the target `tool_name` and its full `arguments` object, usually
   `{"input": {...}}`.
3. Retain `job_id`; poll `ark_job_get` no faster than `poll_after_ms`.
4. Check job status and the original tool's `result.is_error`; read output from
   `result.structured_content`.
5. If that output contains a provider task ID, poll the appropriate provider
   lifecycle tool. Never repeat a create call to check progress.

`ark_job_cancel` stops local work cooperatively; it does not guarantee that
accepted provider work is cancelled. Do not automatically retry a lost
submission response. Stdio jobs belong to the server process that accepted
them; another client process cannot poll them, and the default in-memory
queue loses jobs on restart. Background jobs have no client MCP roots:
configure `OUTPUT_ROOTS` when a job needs an explicit local output path.

See [Tools](../tools.md), [Configuration](../configuration.md), and the
[Python ordinary-job example](python.md) for result handling.

## Troubleshooting

- **Server does not start:** check the resolved executable, checkout, completed
  locked sync, JSON/TOML syntax, and selected execution host.
- **Connected but expected tools are missing:** check private env-file
  metadata, enabled services, feature flags, and inherited-variable shadowing.
  Never request the populated env file or raw validation exceptions in chat.
- **A job ID is unavailable:** ensure you are polling the same process and
  that it has not restarted or expired the result.
- **Edition rejects the configuration:** use that client's guide and official
  source. Do not swap envelope keys blindly or inline keys as a workaround.

## Sources

Official client references are listed in each sibling guide. The shared
launch contract follows the current server configuration.
These protocol/runtime references were consulted on
2026-11-10:

- [MCP local-server connections](https://modelcontextprotocol.io/docs/develop/connect-local-servers)
- [uv project environments and locked execution](https://docs.astral.sh/uv/concepts/projects/sync/)
- [FastMCP client transports](https://gofastmcp.com/clients/transports)
