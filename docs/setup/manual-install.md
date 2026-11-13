---
title: Manual Installation
description: Technical fallback and instructions for an agent or administrator preparing a local Ark MCP installation.
---

Use this page only if you are setting up Ark yourself, or as instructions for
the coding agent handling [guided setup](install.md). Most users should start
with guided setup or [connect to an existing shared server](shared-server.md).
Local installation still needs software preparation; an agent can run these
steps with permission, but an ordinary chat without terminal and file access
cannot.

## Choose an Installation

Select the latest published non-draft, non-prerelease
[GitHub release](https://github.com/byteplus-sa/ark-mcp/releases).
**Checked 2026-11-13:** `v0.3.0` is published and includes the dependency
security refresh with `fastmcp[tasks]>=4.1.8,<4.2`. The historical `v0.2.0`
release locks FastMCP/tasks to `4.0.3`; do not use it for a new installation or
downgrade a patched installation to it.

Verify the selected release's notes, manifest and lockfile before executing
it, rather than assuming every feature on main is released. If no suitable
release exists, ask before selecting a reviewed commit or another explicit
ref; do not silently fall back to main. Never infer that a PyPI package named
`ark-mcp` belongs to this repository.
See [Dependency Security](../security.md#dependency-security).

## Prepare the Software

You need Git, [uv](https://docs.astral.sh/uv/getting-started/installation/),
and Python 3.12 or newer. An agent should check existing tools and ask before
installing missing prerequisites. Do not use sudo or execute an unreviewed
downloaded installation script automatically.

Use a new dedicated directory, not an existing development checkout. These
commands run once in a terminal on the same machine where the client will
launch Ark:

```bash
git clone https://github.com/byteplus-sa/ark-mcp.git
cd ark-mcp
git switch --detach v0.3.0
uv sync --locked --no-dev
```

The example pins the release verified above. Resolve the current release
and record its tag and commit SHA when performing a new setup.

Preserve any existing installation and client entry until the new one passes
verification. Do not reset, clean, or overwrite a dirty checkout, and do not
change dependencies independently of `uv.lock`.

## Prepare Private Credentials

Create a **new, blank** plaintext file named `.env` in a private directory
outside the checkout, shared workspaces and cloud sync. An agent should create
only the blank fields for the products requested, then open it in a local
non-AI text editor for the user.

- macOS/Linux: directory mode `0700`, file mode `0600`; inspect ownership,
  symlinks and ACLs before creation. Use exclusive creation, not overwriting.
- Windows: use an owner-restricted local directory/file ACL. Unix permission
  bits alone do not establish equivalent protection.
- Existing credentials: preserve contents and location; inspect metadata only.
  Ask before repairing permissions or migrating the file.

For images and video, the user fills `BYTEPLUS_MODELARK_API_KEY`. Add other
keys only when needed; see [Provider Keys](../api-keys.md). Match the provider
base URL to the account region. Keep optional feature flags off unless requested.

Never paste keys into chat, client JSON, command arguments, or screenshots.
Do not read back the populated file or `source` it as a shell script. Ark
parses dotenv as data. Recheck file permissions after the user saves it.
Owner-only plaintext is not encrypted; processes under the same OS user can
still read it.

## Register the Client

Resolve these three paths on the **server's execution host**:

| Path | How to resolve it |
| --- | --- |
| uv executable | `command -v uv` in a POSIX shell, or `(Get-Command uv).Source` in PowerShell |
| Ark checkout | Absolute path to the prepared directory containing `pyproject.toml` and `uv.lock` |
| Private credential file | Absolute path to the user's private `.env` |

Use [your client's configuration format](../integrations/index.md). Prefer
its supported registration CLI or settings UI. If UI interaction is required,
produce the resolved non-secret configuration and let the user paste it.
The user should not have to calculate paths already available to the agent.

The following is the **client launch contract**, not an additional command
the user needs to run or keep open:

```json
{
  "command": "/absolute/path/to/uv",
  "args": [
    "--directory", "/absolute/path/to/ark-mcp",
    "run", "--locked", "--no-sync", "python", "-m", "ark_mcp"
  ],
  "env": {
    "ARK_MCP_ENV_FILE": "/absolute/path/to/private/ark-mcp/.env",
    "MCP_TRANSPORT": "stdio",
    "MCP_AUTH_MODE": "local"
  }
}
```

The client starts Ark automatically using this entry. `--no-sync` assumes
the locked installation above succeeded. GUI clients may not inherit the
terminal's PATH. JSON does not universally expand `~`, `$HOME`, or
`${workspaceFolder}`. Preserve spaces and use host-native paths.

Default to user-level registration unless the user asks for project-only
scope. Back up existing client configuration privately, preserve other
servers/comments, and stop if someone edits the file concurrently. Ask before
replacing a same-name entry or changing project trust. Never print the whole
config: other servers may contain inline secrets.

Remote SSH, WSL and container workspaces need matching execution-host paths.
Do not copy provider credentials to another host without approval.

## Verify Without Spending

Use the [discovery request](../integrations/index.md#verify-without-paid-calls)
from the actual target client. Check requested services and job targets, not
just a green connection indicator. Discovery verifies configuration loading,
not provider-key validity or account entitlement.

An agent performing a separate startup preflight should use a private
temporary `ARTIFACT_DIR`, `ARTIFACT_BACKEND=filesystem`, and
`FASTMCP_DOCKET_URL=memory://`. Disable optional probes for that process with
`BYTEPLUS_MODELARK_PRICING_REFRESH_SECONDS=0` and
`READINESS_CHECK_PROVIDERS=false`. Do not save these overrides into normal
client configuration. Suppress raw validation exceptions and stderr; report
only failure status and invalid field names, never values.

Ask before restarting a client with active jobs. If verification fails, keep
the previous installation active and report what remains incomplete.

## Why Not a Generic Install Command?

[FastMCP registration helpers](https://gofastmcp.com/cli/install-mcp) can
generate client configuration, but do not by themselves install a reviewed
Ark revision, establish private credentials, or guarantee this launch contract.
Their `--env-file` option imports variables into generated client configuration;
it is not equivalent to passing Ark the non-secret `ARK_MCP_ENV_FILE` path.
Do not substitute it for the private-file workflow.

Ark currently ships no one-click installer. A shorter command is not a better
setup path if it loses locked dependencies, private credential handling, or
the correct client format.
