---
title: Connect to a Shared Server
description: Use an existing administrator-managed Ark MCP server without installing Python, Git or uv locally.
---

**Already have an Ark server URL from your team? Skip local installation.**
Your administrator runs the server; your client connects over HTTPS.
You do not need Python, Git, uv, a local checkout, or a local provider-key file.

This repository does not provide a public hosted endpoint. The documentation
website is not an MCP server. Without an existing managed endpoint, use
[guided local setup](install.md).

## What You Need

Ask your administrator for:

- The exact HTTPS MCP endpoint, including its path.
- The shared MCP key through a private secret-sharing channel and
  bearer-header configuration instructions for your client.
- Access to the tools you intend to use.

Use only a server you trust: it receives your prompts and media references,
and its operator configures provider credentials and spending limits.
Connecting to a server does not mean generation is free.

## Connect

1. Open your client's MCP or connector settings.
2. Choose the supported **remote/HTTP** connection option, not a local command.
3. Enter the administrator-provided endpoint and configure authentication
   through the client's supported private credential mechanism.
4. Review the server/tool permissions and enable the connection.

Labels and remote-auth support differ by client and edition. This server
accepts only an administrator-issued shared MCP key for hosted HTTP. Use your
client's private `Authorization: Bearer <shared MCP key>` configuration.
There is no OAuth login or discovery flow. All members use the fixed
`shared-team` identity and ordinary permissions, share data and spending limits,
and must update their clients when the administrator rotates the key.
Account-wide asset and moderation administration is unavailable.
If your client cannot send the required credentials securely, ask the
administrator for a supported integration. Do not disable authentication
or paste tokens into chat to get connected.

The provider API keys belong on the server, not in your chat. Your connection
token is also secret and is not the same as a ModelArk provider key.

## Verify

Ask your connected client:

```text
Use the Ark MCP server I just connected. Call only ark_list_models and
ark_job_capabilities, and report my available services and tools.
Do not generate, understand, transcribe, upload, or submit jobs.
```

Missing tools may reflect the fixed team permissions or server configuration.
Ask the administrator rather than changing your local environment. Discovery
does not validate provider entitlement.

Continue with [Your First Tool Call](first-run.md).

## Local Files and Outputs

A remote server cannot read or write files on your computer using a local
path. Use the tool's supported media inputs, approved uploads or artifact
downloads. Do not send a private file until you understand where it goes.

## For Administrators

Deploying a server is separate from connecting to one. See
[Self-Hosting](../self-hosting.md) for the protected deployment kit and
[Transports](../transports.md) for shared-key auth, TLS, scopes, Host/Origin guards and
shared-process behavior. For BytePlus provisioning, start with
[deployment requirements](../byteplus-terraform.md#requirements),
[dependency installation](../byteplus-terraform.md#set-up-dependencies), and
[IAM and credential setup](../byteplus-terraform.md#set-up-iam-and-credentials).
Administrators can maintain provider keys through
[pinned Secret Manager versions](../secret-manager.md); members still receive
only the separate shared MCP connection key.
These are administrator requirements, not requirements for members connecting
to an existing server. Do not expose local-auth mode to a network.
