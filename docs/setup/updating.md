---
title: Update and Roll Back
description: Stage a reviewed Ark MCP release without overwriting credentials, active jobs or development work.
---

Updating the repository does not automatically update a client connection.
The client launches the checkout or installed package recorded in its config.

## Before Updating

- Check active worker jobs and provider tasks. Save provider task IDs.
- Read [release notes](../releases.md) and applicable migration guidance.
- Preserve your private env file, client config, artifact storage and runtime
  database. Do not copy `.env.example` over existing credentials.
- Inspect the current checkout for changes. Never reset or clean a user's
  development tree as part of an upgrade.

## Stage a Release

Query published stable releases rather than assuming `main` is stable.
Create a separate checkout of the selected published
release and run `uv sync --locked --no-dev` there. Use isolated temporary
runtime and artifact state for preflight so validation does not mutate your
active installation's database.

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
before activation.

Do not automatically delete older checkouts, backups, objects or artifacts.
For migration from the former per-model/per-operation tool surface, read
[Migrating to 0.2.0](../migration-0.2.md).
