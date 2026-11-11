# Getting Started

Connect Ark MCP to your AI client, then ask it to create or understand media.
Your client handles tool calls and starts the local server automatically.
You do not need to keep a terminal open.

## Installation

Start with [Set Up Ark MCP](setup/install.md). A terminal-capable coding
agent can prepare the installation and client configuration with your
permission. If your administrator already provides an endpoint, use
[Connect to a Shared Server](setup/shared-server.md) and skip local setup.

## Configuration

For local setup, enter the [provider keys](api-keys.md) you need in the
private file prepared for you. Never paste keys into chat or shared client
configuration. Images and video use ModelArk; audio generation and
speech-to-text use Seed Speech. You do not need every service.

## Running the Server

Enable `ark-seed` in your client's MCP settings. The saved configuration
tells the client how to start Ark; a manual server-start command is not an
additional user step. For a shared server, your administrator handles startup.

## Verifying Your Setup

Ask your connected client:

```text
Check my Ark connection with ark_list_models and ark_job_capabilities.
List the services and tools available to me. Do not generate, understand,
transcribe, upload, or submit jobs.
```

Confirm that the tools you need appear in actual results. Discovery makes
no provider calls; it does not validate provider keys or account entitlement.
See [Troubleshooting](troubleshooting.md) if the connection or tools are missing.

## Using with MCP Clients

Choose [your client guide](integrations/index.md), then try
[Your First Tool Call](setup/first-run.md). You can ask in plain language;
exact JSON envelopes are available as optional technical details.

## Technical Setup

Installing without an agent? Use [Manual Installation](setup/manual-install.md).
Developing or deploying the server? Use [Transports](transports.md),
[Background Execution](integration-guide.md#background-execution), and
[Configuration](configuration.md). These are not prerequisites for ordinary use.

<span id="prerequisites"></span>
<span id="stdio-default-for-local-mcp-clients"></span>
<span id="streamable-http-loopback-development"></span>
<span id="dev-mode-with-auto-reload"></span>
<span id="claude-desktop"></span>
<span id="mcp-inspector"></span>

## Next Steps

- [Choose a Tool](capabilities.md) for images, video, audio, understanding, 3D and avatars.
- [Update and Roll Back](setup/updating.md) for an existing installation.
- [Migrating to 0.2.0](migration-0.2.md) when upgrading old 0.1.x prompts or clients.
