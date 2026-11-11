---
title: Client Integrations
description: Connect local MCP clients to Ark with private credentials and non-billable discovery.
---

Choose the client you want to use. **You configure the connection once; the
client starts Ark automatically.** You do not need to run the launch command
yourself whenever you open a chat.

## Choose a Client

| Client | Easiest starting point |
| --- | --- |
| [Claude Desktop](claude-desktop.md) | Have a coding agent prepare its configuration, then enable it in Desktop |
| [Claude Code](claude-code.md) | Ask Claude Code to perform guided setup |
| [Codex](codex.md) | Ask a terminal-enabled Codex session to perform guided setup |
| [TRAE](trae.md) | Ask its coding agent to prepare setup; confirm any MCP settings UI steps |
| [Cursor](cursor.md) | Use a file/terminal-enabled agent for guided setup |
| [VS Code](vscode.md) | Use an enabled coding agent, then review the MCP connection |
| [OpenCode](opencode.md) | Ask OpenCode to perform guided setup for its installed version |
| [Python](python.md) | Developer example for building your own client |

Use the [guided setup request](../setup/install.md#guided-local-setup) before
opening manual configuration snippets. It requires terminal and file-editing
access; ordinary chat alone cannot install software. For a team-provided
endpoint, [connect over HTTPS](../setup/shared-server.md) instead.

Default to user-level registration so Ark is available across projects.
Choose project-only scope when you need it. Your agent should preserve other
MCP entries and ask before replacing an existing connection.

## Prepare the Launch

Your setup agent resolves the executable, checkout and private credential-file
paths, installs the reviewed locked environment, and prepares the correct
client entry. You only need to approve changes, enter keys privately and
complete any required UI confirmation.

The fallback snippets in each client guide are for manual setup or
troubleshooting, not additional steps after guided setup. Their prerequisites
and exact launch contract are in [Manual Installation](../setup/manual-install.md).
Do not paste unresolved placeholder paths into a client.

## Keep Credentials Private

Enter only the [keys you need](../api-keys.md) in a private local text editor.
The file stays outside your repository and shared folders; client configuration
contains its path, not key values. Preserve existing credentials. Never paste
populated configuration, raw logs or validation errors into chat.

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

The setup agent's isolated preflight procedure is in
[Manual Installation](../setup/manual-install.md#verify-without-spending).
There is no need for users to run a separate validation script.

## Long-Running Work

Ask your client to use Ark's ordinary background-job tools and report the
result when ready. It handles submission and polling; you do not need to
copy job JSON manually. Start with the [dry-run request](../setup/first-run.md)
before approving paid generation.

Keep the connection open while work runs. Restarting loses default in-memory
jobs, and another local client cannot poll them. Never repeat a generation
just to check progress. Technical result handling is in
[Background Execution](../integration-guide.md#background-execution).

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
