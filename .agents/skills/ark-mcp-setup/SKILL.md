---
name: ark-mcp-setup
description: Install or update the Ark MCP server in TRAE, Claude, Codex, or another agent. Detect the client, default to global scope, prefer stable releases, and guide private API-key entry.
---

# Ark MCP Setup

Install the server into the user's preferred MCP client, not the client
application itself. Use this skill for onboarding, updates, registration,
and credential-file setup. Use `ark-mcp` for generating or understanding media.
Do not install an editor or CLI, change the agent's model provider, or deploy
public HTTP as a side effect.

Read [platforms.md](references/platforms.md) before selecting paths or running
commands. This skill must work independently of its author's device or a
repository checkout. Resolve the current user's home, OS, shell, installed
tools, and execution host each time; never reuse paths or account settings
from a previous session.

## 1. Resolve the Client and Scope

1. Prefer the user's named client over detection. Otherwise use explicit
   runtime host metadata to identify the current agent. Distinguish the
   hosting IDE from an embedded agent: Codex inside TRAE might mean either.
2. Use executable availability and config-directory existence only as
   supporting evidence, never proof of the active agent. Do not dump the
   environment, process arguments, or configuration files to identify it.
3. If ambiguous, ask one short question with the likely clients. Distinguish
   Claude Code from Claude Desktop, and TRAE IDE from TRAE Work and CN from
   International. Never configure every installed client automatically.
4. Default to **global/user-level MCP registration**. State the detected
   client, the evidence, and the default before making changes; honor an
   explicit project/local preference without asking again. If project scope
   is requested, resolve the intended workspace, not this skill's directory.
5. Confirm where the server actually runs: local OS, WSL, SSH host, container,
   or cloud sandbox. Commands, paths, credentials, and client registration
   must refer to the same runtime. A local path cannot configure a remote
   sandbox. Ask before copying credentials to another machine.

Read [clients.md](references/clients.md) for the selected client only.
Global MCP registration is separate from installing this skill globally.
Create or distribute skills through the host's skill installer only when
requested; changing MCP scope does not authorize changes to all skill roots.

## 2. Resolve an Installation

- Default upstream: `https://github.com/byteplus-sa/ark-mcp.git`.
  Check existing origins against that repository; accept its equivalent
  GitHub SSH URL. Ask before using a fork or a different remote. Never put
  access tokens in Git URLs or shell arguments.
- Reuse a verified existing installation only when appropriate. Prefer a
  dedicated managed checkout instead of modifying the user's development
  branch. Do not overwrite dirty, divergent, or unrelated working trees.
- Resolve the managed root using [platforms.md](references/platforms.md),
  honoring user paths and platform-managed locations. Use versioned checkout
  directories under `releases/`, keeping the previous working version
  available for rollback.
- Project scope controls visibility, not credential sharing. It may reuse a
  managed checkout outside the project. Isolate runtime state and credentials
  separately when the user needs independent installations.
- Check `git`, `uv`, and the selected release's Python requirement. Use its
  `pyproject.toml` and `uv.lock`, not a version memorized in this skill.
  Obtain approval before installing missing system tooling. Do not use sudo
  or unreviewed curl-to-shell installation as an automatic fallback.

## 3. Select and Stage the Latest Stable Release

Read [releases.md](references/releases.md) before downloading or updating.
Resolve the latest published non-draft, non-prerelease GitHub release at
execution time, then record its tag and resolved commit SHA.

If no stable release exists, ask whether to use the current default-branch
commit, select an explicit ref, or stop. A network/authentication failure is
not evidence that no releases exist. Never silently fall back to `main`.

Stage the selected commit in a new managed checkout, detached from any
development branch. Inspect its setup instructions and migration notes
before executing downloaded code. In that directory run:

```bash
uv sync --locked --no-dev
```

Do not upgrade dependencies independently or rewrite `uv.lock`. Use absolute
paths and an argument array in client configuration. Keep the prior version
and MCP entry until the staged version passes verification.

## 4. Prepare Private Credential Entry

Read [credentials.md](references/credentials.md) before handling credentials.

1. Ask which capabilities are needed if unspecified. ModelArk, Seed Speech,
   VOD MediaKit, and Vision AI use different credentials; never assume one key
   unlocks everything. Keep whitelist and destructive-operation flags off.
2. Create a minimal blank `.env` in a private directory outside the checkout,
   with owner-only access. Preserve an existing file without reading or
   overwriting its values.
3. **Open the file in a local, non-AI text editor** using a native opener or
   the user's chosen editor. Ask the user to enter keys, save, and confirm
   completion. Never ask them to paste credentials into chat.
4. Do not read back, screenshot, diff, upload, or index the populated file.
   Verification may load it inside the server process, but must return only
   sanitized status, field names, and enabled capability names.
5. Set `ARK_MCP_ENV_FILE` to the absolute credential-file path in the MCP
   entry. Store no credential values in the entry.

Permissions protect against other ordinary OS users, not an agent or process
running as the same user. Explain that `.env` is plaintext, not a secrets
vault. Do not promise the agent technically cannot access the file.

## 5. Register Without Damaging Existing Configuration

Use the selected client's supported CLI or configuration UI where possible;
verify local `--help` before relying on CLI flags. Follow
[clients.md](references/clients.md).

- Default server name: `ark-seed`. Inspect only non-secret metadata for that
  name, including scope and command path, plus possible shadowing entries.
- If the name belongs to another server, ask before replacing it. If it is
  the intended installation, update only that entry after staging succeeds.
- Make an owner-only backup before mutation. Client configurations can
  contain unrelated tokens: never print the full file or full diff, and
  preserve permissions on backups.
- Parse JSON/TOML using an appropriate parser; preserve unrelated fields and
  comments with a format-aware edit. Never use regex-based whole-file
  rewrites. Detect concurrent changes and stop rather than overwrite them.
- Global is the default, not permission to override an explicit project
  choice. If the client does not support that scope, explain and ask.
- Do not delete lower/higher-scope entries to resolve shadowing without
  consent. Project-local config with absolute machine paths should not be
  committed automatically.

Use this launch contract, adapting only the client's envelope:

```json
{
  "command": "/absolute/path/to/uv",
  "args": [
    "--directory", "/absolute/path/to/managed/checkout",
    "run", "--locked", "--no-sync", "python", "-m", "ark_mcp"
  ],
  "env": {
    "ARK_MCP_ENV_FILE": "/absolute/path/to/private/.env",
    "MCP_TRANSPORT": "stdio",
    "MCP_AUTH_MODE": "local"
  }
}
```

Resolve the `uv` executable on the server host. Do not rely on shell expansion
of `~`, `$HOME`, or `${workspaceFolder}` inside arbitrary client JSON.
The launch contract requires the successful locked sync above.

## 6. Verify and Finish

1. Check credential-file ownership/permissions and config syntax without
   displaying values. Validate settings with exception text suppressed;
   report only invalid field names, never Pydantic input values.
2. Launch the staged server with the exact command, args, environment, and
   intended credential file using an MCP client. Initialize, list tools,
   read `seed-health://status`, and call `ark_list_models` with
   `{"input": {}}` (verify the selected release's schema). Do not run paid
   generation or uploads for setup verification. For this standalone
   preflight process only, set `ARTIFACT_DIR` to a private temporary
   directory, `ARTIFACT_BACKEND=filesystem`, `FASTMCP_DOCKET_URL=memory://`,
   `BYTEPLUS_MODELARK_PRICING_REFRESH_SECONDS=0`, and
   `READINESS_CHECK_PROVIDERS=false`. Keep active local/remote state and
   optional provider probes out of preflight; do not persist these
   overrides into the user's MCP entry.
3. Ensure the requested product tools register; zero provider credentials
   can produce a healthy server with no generation tools. Tool registration
   proves configuration loading, not provider authentication or entitlement.
4. Ask before stopping active jobs or restarting the user's client. Stdio
   jobs belong to the old process and may be lost. Reload/restart MCP and
   verify discovery from the actual target agent when that interface is
   available. Otherwise report that target-client verification is pending.
5. On failure, leave the old installation active. If registration already
   changed, restore only the affected entry from the protected backup,
   checking for concurrent edits first. Never roll back credentials blindly.
6. Report the client and scope, tag/SHA, checkout path, credential-file path
   only, verification results, restart needs, and any unresolved setup.
   Do not say "installed successfully" when a UI step or credential entry
   is still pending.

For repeat runs, keep an already-current installation unchanged unless repair
is needed. Preserve credentials and user-selected flags during updates.
