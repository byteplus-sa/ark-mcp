---
title: Set Up Ark MCP
description: Let a coding agent prepare your local connection, or connect to a server your team already runs.
---

**Set up once, then use Ark from your AI client.** The client starts a local
Ark server automatically when needed. You do not need ComfyUI, a terminal
left open, or a separate server-start command each time.

## Choose Your Path

| Your situation | Start here |
| --- | --- |
| You use a coding agent with terminal and file-editing access | [Guided local setup](#guided-local-setup) |
| Your team already runs Ark and gave you a server URL | [Connect to that server](shared-server.md), with no local installation |
| Ark is already installed on your machine | [Connect your client](../integrations/index.md) |
| You have no setup-capable agent or prefer doing it yourself | [Manual installation](manual-install.md), the technical fallback |

## Guided Local Setup

Use a coding agent that can run terminal commands and edit files on your
machine, such as an appropriately enabled Claude Code, Codex, TRAE IDE,
Cursor, VS Code agent, or OpenCode session. Approve only the setup actions
you understand. An ordinary chat without those tools cannot install software.

### 1. Ask Your Agent

Copy this request into that agent. Replace the client and desired capabilities:

```text
Set up https://github.com/byteplus-sa/ark-mcp for [my client],
user-wide, on this machine. I need [images and video].
Read https://byteplus-sa.github.io/ark-mcp/setup/manual-install/
and my client's integration guide before making changes.
Use the reviewed patched revision, check prerequisites, and ask before
installing missing software. Preserve existing projects and MCP entries.
Resolve paths and prepare the client configuration for me.
Open a private blank credential file in a local non-AI text editor;
I will enter my keys there. Do not read back the populated file
or ask me to paste keys into chat.
Verify with discovery only, without generating, uploading, or spending.
Ask before restarting a client with active jobs. Report any unfinished steps.
```

This is **agent-assisted setup, not a one-click installer**. The agent performs
the one-time software and configuration steps; you approve changes and enter
credentials. If it cannot access the instructions or required tools, stop
and use the manual fallback or ask your administrator for help.

For **Claude Desktop**, run the request in a separate coding agent and name
Claude Desktop as the target. Desktop chat alone is not assumed to have
installation access.

### 2. Enter Your Keys Privately

You need a BytePlus account and access to the products you choose. Enter only
those keys in the private file the agent prepares, save, then tell it you are
done. Never paste keys into chat or client configuration.

| What you want | Key to obtain |
| --- | --- |
| Images, video, image/video/audio understanding | ModelArk |
| Audio generation and speech-to-text | Seed Speech |
| Video enhancement, transcoding, subtitles, audio separation | VOD AI MediaKit |
| Talking avatars | Vision AI, plus OmniHuman access |

See [Provider Keys](../api-keys.md) for where to obtain each key. 3D, premium
models and other optional products have extra access requirements. You do
not need to configure every service.

The credential file is private plaintext, not a secrets vault; software
running as your OS user can still read it.

### 3. Enable and Verify

Follow any remaining step in [your client guide](../integrations/index.md).
Some clients require you to paste the agent-prepared configuration into a
settings screen or approve server access.

Then ask your client:

```text
Check the ark-seed connection using only ark_list_models and
ark_job_capabilities. Tell me which services and tools are available.
Do not generate media, run understanding, transcribe, upload, or submit jobs.
```

Success means the expected tools return discovery results. A green connection
indicator alone is not enough. Discovery makes no provider calls and does not
prove provider-key validity or account entitlement. If tools are missing,
see [Troubleshooting](../troubleshooting.md).

Continue with [Your First Tool Call](first-run.md).

<span id="prerequisites"></span>
<span id="choose-an-installation"></span>
<span id="enter-credentials-privately"></span>
<span id="connect-a-client"></span>
<span id="verify"></span>

## Technical Details

Path resolution, locked dependencies, private-file permissions and the
client launch contract are documented once in
[Manual Installation](manual-install.md). They are instructions for your
setup agent or administrator, not extra steps after guided setup.

The published `v0.2.0` release predates the security refresh; use the
[reviewed revision](manual-install.md#choose-an-installation) instead.
This unversioned site describes its reviewed main build, not every historical
release.
