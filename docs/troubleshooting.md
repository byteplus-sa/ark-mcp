# Troubleshooting

## SSL Certificate Errors

On macOS, the uv-installed Python may not use the system Keychain for TLS
verification. If you see `CERTIFICATE_VERIFY_FAILED`, install and enable
`truststore`:

```bash
uv add truststore
```

The server already injects `truststore` at startup in `__main__.py` and
`server.py`. If running scripts that bypass the server module, add:

```python
import truststore
truststore.inject_into_ssl()
```

## "API key not configured" Error

If a tool raises `BYTEPLUS_*_API_KEY is not configured`, the server did not
find the credential in the environment. Check:

1. `.env` file exists at the project root
2. The key name matches exactly (`BYTEPLUS_MODELARK_API_KEY` or
   `BYTEPLUS_SEED_SPEECH_API_KEY`)
3. No leading/trailing whitespace in the value
4. Run `make check-env` to validate

When a credential is absent, the server skips registering that product's
tools — it does not register a broken tool.

## Model Not Found / Not Activated

If the provider returns `403 FORBIDDEN` with "model not activated":

1. Check the model ID in `.env` matches your console
2. Confirm the model is activated in your BytePlus region
3. Verify the base URL matches your key's region

Model IDs are region-scoped and account-specific. The defaults in
`.env.example` may not match your account. Run `make check-env` to validate
your configuration, then adjust the model IDs before making billable calls.

## Provider URL Expired

Provider output URLs expire:

- **Seed Audio**: 2 hours
- **Seedream**: 24 hours
- **Seedance**: 24 hours (video and last-frame)

The server persists outputs immediately by default (`persist=True`) and
returns `seed-media://artifacts/{id}` resource references that do not
expire (until the artifact TTL, default 7 days).

If you set `persist=False`, the tool returns the raw provider URL which
will expire. Use the artifact resource instead.

## Seedance Task States

| Status | Meaning |
|---|---|
| `queued` | Task is waiting to start |
| `running` | Task is generating |
| `succeeded` | Task completed, video available |
| `failed` | Task failed, check `error` field |
| `expired` | Task expired before completion |
| `cancelled` | Task was cancelled via DELETE |

### Cannot Cancel or Delete

- `running` tasks cannot be cancelled or deleted — wait for completion
- `cancelled` tasks cannot be deleted

The `seedance_cancel_or_delete_task` tool requires `expected_status` to
match the actual status, preventing accidental destructive actions.

## Timeout on Long Generations

Seedream Pro and long Seed Audio generation can take 1-5 minutes. The
default request timeout is 10 minutes (`BYTEPLUS_REQUEST_TIMEOUT_MS=600000`).

If you experience timeouts:

1. Increase `BYTEPLUS_REQUEST_TIMEOUT_MS`
2. Check your network connectivity to the BytePlus region
3. Note: a timeout does **not** mean the operation failed — it may have
   succeeded upstream. The error is marked `ambiguous_completion=True`.
   Do not retry blindly.

## Client Timeouts vs Provider Latency

A client-side MCP timeout is distinct from a server failure. Seedream Pro and
Seedance provider submissions can take 60–150 s (longer under cold start or
provider queue), so the server requires background execution for those
operations rather than keeping the original foreground request open.

- On a task-capable client, invoke `seedance_create_task`,
  `seedream_generate_image`, and `seed_audio_generate` with MCP task metadata.
  Retain the returned MCP task ID and poll `tasks/get` at the advertised
  interval until terminal.
- On an ordinary-only client, use the `ark_job_*` workflow below. Its terminal
  result contains the same typed tool output.
- For long video work, the background result contains the provider task ID.
  Poll `seedance_get_task` in the foreground with `persist_output=false`;
  run it in the background with `persist_output=true` when the completed output
  needs persistence.
- If a client disconnects after task acceptance, reconcile with the returned
  MCP task ID or provider task ID rather than resubmitting (see
  `ambiguous_completion`).

### Client rejects task-augmented execution

If Codex, Cursor, or another MCP client reports that the server requires
task-augmented execution but its transport does not support it, do not call the
required-task provider tool directly and do not switch it to foreground mode.

1. Call `ark_job_capabilities` and confirm the target is listed.
2. Call `ark_job_submit` with the target tool name and its original arguments.
3. Retain the returned Ark `job_id` and poll `ark_job_get` at
   `poll_after_ms` until terminal.
4. Read the original tool output from `result.structured_content`.

This remains a real background task on the same worker. For a Seedance, Seed 3D,
or VOD submission, the provider task ID appears inside the completed original
result and must be retained separately. A missing target usually means its
provider credential, feature flag, or JWT scope is unavailable.

## Artifacts Not Persisting

Check:

1. `ARTIFACT_DIR` is writable
2. Disk has sufficient space
3. `ARTIFACT_BACKEND` is set to `filesystem` (the only MVP backend)

Run `make check-env` to validate configuration.

## HTTP Server Refuses to Start

A non-loopback HTTP bind is rejected in `local` auth mode. Set
`MCP_AUTH_MODE=jwt` and configure `MCP_JWT_JWKS_URI`, `MCP_JWT_ISSUER`, and
`MCP_JWT_AUDIENCE`. Add the public hostname to `MCP_ALLOWED_HOSTS` and browser
origins to `MCP_ALLOWED_ORIGINS`.

## Unknown Tool over HTTP

FastMCP hides tools whose required scopes are absent. Verify the Bearer token
has the appropriate `seed:audio:generate`, `seedream:generate`,
`seedance:create`, `seedance:read`, `seedance:delete`, or `artifacts:read`
scope and contains both a principal (`sub`) and configured tenant claim.

## Running Tests

```bash
make test          # run all tests
make lint          # ruff check
make typecheck     # mypy
```

Tests use `respx` and an autouse socket guard—real network calls fail the suite.
