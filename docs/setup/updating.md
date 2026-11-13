---
title: Update and Roll Back
description: Stage a reviewed Ark MCP release without overwriting credentials, active jobs or development work.
---

Updating the repository does not automatically update a client connection.
The client launches the checkout or installed package recorded in its config.

## Recommended Update

For a shared server, ask its administrator to handle updates. For a local
installation, use the same terminal-capable coding agent that performed
[guided setup](install.md):

```text
Update my existing ark-seed connection for [my client].
Read https://byteplus-sa.github.io/ark-mcp/setup/manual-install/
and select the latest published release after reviewing its migration
and security notes. Do not use the old unpatched v0.2.0 release.
Preserve my keys, client settings, projects, artifacts and runtime state.
Stage and verify the update separately using discovery only.
Do not read back credentials or run paid tests.
Ask before switching the connection or restarting active jobs.
Keep the previous patched installation available for rollback.
```

Review the version and any migration requirements before approving the
switch. Afterward, run the [connection check](../integrations/index.md#verify-without-paid-calls).
No separate server-start command is needed.

<details>
<summary>Manual update, activation and rollback details</summary>

## Before Updating

- Check active worker jobs and provider tasks. Save provider task IDs.
- Read [release notes](../releases.md) and applicable migration guidance.
- Preserve your private env file, client config, artifact storage and runtime
  database. Do not copy `.env.example` over existing credentials.
- Inspect the current checkout for changes. Never reset or clean a user's
  development tree as part of an upgrade.

## Stage a Patched Revision

Choose a reviewed revision containing the dependency security refresh and
the supported `fastmcp[tasks]>=4.1.8,<4.2` runtime. Published `v0.3.0`
includes that refresh; the historical `v0.2.0` release is unpatched.
Do not stage v0.2.0 as an update or downgrade a patched installation to it.
Follow [release selection](manual-install.md#choose-an-installation); ask
before using a reviewed commit when no suitable release exists.

For a newer release, verify its notes, `pyproject.toml`, and `uv.lock` rather
than trusting the stable tag alone. This site tracks its reviewed `main`
build, not every historical release. Do not label a commit checkout as stable.

Create a separate checkout of the selected patched revision and run
`uv sync --locked --no-dev` there. Use isolated temporary runtime and artifact
state for preflight so validation does not mutate your active installation's
database.

Verify startup, tool discovery and job capabilities without paid generation.
These checks do not prove key validity or provider entitlement. New env
variables and model gates should be reviewed before switching.

## Activate

Change only the `ark-seed` launch path in your client config, preserving other
servers and the existing private `ARK_MCP_ENV_FILE` path. Check that no one
edited the config concurrently.

After active work is finished, reconnect the client and repeat discovery.
Keep the old installation until verification succeeds. Default in-memory
worker jobs do not survive restarting the server.

## Roll Back

Restore the previous launch entry and reconnect. Keep credentials and media
unchanged. Code rollback alone is not enough if an upgrade performed an
irreversible database migration; assess backup and restoration requirements
before activation. Select a patched rollback target rather than restoring a
dependency set with known security issues.

Do not automatically delete older checkouts, backups, objects or artifacts.
For migration from the former per-model/per-operation tool surface, read
[Migrating to 0.2.0](../migration-0.2.md).

</details>
