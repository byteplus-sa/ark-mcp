---
title: Install Ark MCP
description: Prepare a locked Ark MCP installation and private credentials before connecting your client.
---

Ark MCP is a Python MCP server that your AI client runs locally over stdio.
You do not need ComfyUI. For remote access, use the protected
[HTTP deployment](../self-hosting.md) instead.

## Prerequisites

- Python 3.12 or newer.
- [uv](https://docs.astral.sh/uv/getting-started/installation/) and Git.
- A compatible MCP client.
- A BytePlus account with access to the products you intend to use.

Provider calls can incur charges. You can verify startup and discovery without
generating media. A successful connection does not prove provider entitlement.

## Choose an Installation

Inspect the
[published releases](https://github.com/byteplus-sa/ark-mcp/releases) and
select a reviewed release tag. Do not assume a package named `ark-mcp` on
PyPI belongs to this repository.

```bash
git clone https://github.com/byteplus-sa/ark-mcp.git
cd ark-mcp
git switch --detach <published-release-tag>
uv sync --locked --no-dev
```

Replace `<published-release-tag>` with an actual published tag before running
the command. If no stable release is published, choose a specific reviewed
commit explicitly, or use the default branch consciously for development.
Do not label a branch checkout as a stable release. Never run these operations
against an existing dirty checkout as an update shortcut.

## Enter Credentials Privately

Use a private plaintext `.env` outside the checkout, shared workspaces, and
cloud-synced directories. On macOS/Linux, create an owner-only directory and
file, checking that the destination is new and not a symlink:

```bash
mkdir -m 700 "$HOME/.config/ark-mcp"
install -m 600 .env.example "$HOME/.config/ark-mcp/.env"
```

These commands are for a **new destination**. If either already exists,
preserve it and inspect its ownership and permissions instead of overwriting
it. On Windows, use owner-restricted ACLs and host-native absolute paths.
Open the file in a local non-AI text editor and fill only the needed keys.
Never paste keys into chat, client JSON, command arguments or screenshots.
Do not `source` the file; Ark parses dotenv as data.

| Product | Minimum configuration |
| --- | --- |
| Seedream, Seedance, image/video understanding, audio understanding | `BYTEPLUS_MODELARK_API_KEY` |
| Seed Audio and speech-to-text | `BYTEPLUS_SEED_SPEECH_API_KEY` |
| VOD AI MediaKit | `BYTEPLUS_VOD_MEDIAKIT_API_KEY` |
| OmniHuman | `BYTEPLUS_CV_API_KEY` and `BYTEPLUS_OMNIHUMAN_ENABLED=true` |
| Hyper3D and Hitem3d | ModelArk key and `BYTEPLUS_MODELARK_3D_ENABLED=true` |

The keys are not interchangeable. Asset-library operations,
object storage and premium models have additional requirements. See
[Provider Keys](../api-keys.md) and [Configuration](../configuration.md).
Match your ModelArk base URL to your account region.

Owner-only plaintext is not a secrets vault: processes running as the same
OS user can still read it. Recheck permissions after editing.

## Connect a Client

Resolve the actual `uv` executable with `command -v uv` on a POSIX shell.
Record the absolute checkout and credential-file paths. GUI clients may not
inherit your terminal's `PATH`.

Each local client should launch:

```text
/absolute/path/to/uv --directory /absolute/path/to/ark-mcp run --locked --no-sync python -m ark_mcp
```

Set these non-secret environment settings in the client configuration:

```json
{
  "ARK_MCP_ENV_FILE": "/absolute/path/to/private/ark-mcp/.env",
  "MCP_TRANSPORT": "stdio",
  "MCP_AUTH_MODE": "local"
}
```

Use the [guide for your client](../integrations/index.md). Configuration
envelopes differ across Claude, Codex, VS Code and OpenCode; do not reuse
another client's format blindly. `--no-sync` assumes the locked dependencies
were installed successfully.

## Verify

Enable the server after reviewing your client's trust prompt. Ask it to list
tools and call only `ark_list_models` with `{"input": {}}` and
`ark_job_capabilities` with `{}`. Both tools make no provider calls.
Optional background pricing refresh is separate from discovery; isolated
preflight settings are described in the
[integration guide](../integrations/index.md#verify-without-paid-calls).

If tools are missing, check env-file path, key names and feature flags.
Restart after changing startup configuration, taking care not to interrupt
active jobs. Continue to [Your First Tool Call](first-run.md).
