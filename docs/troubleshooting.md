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

## `ark_job_capabilities` Returns No Targets

An empty `targets` list, or a client reporting only the six always-on job and
artifact tools, means no provider tools are registered in **the server process
that client is talking to**. Work through these in order; the first two are by
far the most common and neither is a server fault.

**1. The client is holding a stale connection.** Credentials and tool
registration are resolved once at startup, so a running server never picks up a
later env-file edit. Restarting the application is not always enough: an
existing session can keep its established stdio connection while new processes
spawn beside it. Reconnect that server, or open a fresh session, and check the
tool count again.

**2. You are looking at another application's processes.** Every MCP client
that is configured for this server runs its own copy. A `ps` listing therefore
shows processes belonging to other editors, and one of those being hours old
proves nothing about your client. Attribute each process to its owner before
concluding anything:

```bash
ps -eo pid,ppid,lstart,command | grep ark_mcp | grep -v grep
```

Then walk each `ppid` up with `ps -o comm= -p <ppid>` until you reach the
application. Confirm the process you are reasoning about belongs to the client
you are using.

**3. The process started before the credential was added.** Same single-read
rule as above: check the process start time against the env file's mtime
(`stat -f "%Sm" .env`). If the process is older, restart it.

**4. The server never found the env file.** `.env` is resolved relative to the
server's **working directory**, not the repository root, so a client that
spawns the server elsewhere finds nothing. This is the least likely cause,
because most clients set the working directory correctly — verify before acting
on it rather than assuming it:

```bash
lsof -a -p <pid> -d cwd
```

The server also logs `no_provider_credentials_configured` at startup in exactly
this case, reporting the env file it looked for and the working directory it
used. **If that warning is absent from the log, this is not your problem.** If
it is present, either set `ARK_MCP_ENV_FILE` to an absolute path in the MCP
client config, or launch the server with its working directory at the
repository root, e.g. `uv --directory /path/to/ark-mcp run python -m ark_mcp`.

A server that resolved its credentials but is missing only one product's tools
is a different problem: that product's key is absent, or the process predates
it. Cases 1 and 3 apply.

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

### Artifact has `persistence_error`

An `ArtifactRef` with `persistence_error` means the output was generated and
billed but could not be stored durably (after download retries). The call
still succeeded; nothing was lost yet:

- `id="provider-url"`: `uri` is the temporary provider URL, valid until
  `persistence_error.source_url_expires_at`. Call `seed_media_persist_url`
  with that `uri`, `media_type`, `mime_type`, and `source_expires_at` before
  it expires. For Seedance and Seed 3D, polling the get tool again with
  `persist_output=true` also retries persistence.
- `id="inline-fallback"`: the bytes are in `fallback_data` (Base64). Save them
  directly; they are not stored anywhere else.

Check `persistence_error.code` and `retryable`: `output_too_large` and
`untrusted_output_host` will not succeed on retry; `download_failed` and
`storage_failed` usually will. For persistent download timeouts, raise
`ARTIFACT_DOWNLOAD_TIMEOUT_SECONDS` or `ARTIFACT_DOWNLOAD_MAX_ATTEMPTS`.

### `output_path` rejected or `export_error` set

- `... is disabled: no output roots are configured`: the client does not
  advertise MCP roots and `OUTPUT_ROOTS` is empty. Set `OUTPUT_ROOTS` to one
  or more absolute directories.
- `must be inside an allowed output root` / `must be an absolute path`: use an
  absolute path under a client root or an `OUTPUT_ROOTS` entry.
- `only supported in stdio transport mode`: local paths are stdio-only.
- `requires persist=true` / `requires persist_output=true`: local copies are
  written from the durable artifact.
- `already exists with different content`: pass `overwrite=true`.

Path errors are raised before the provider is called. A failed write after
generation is reported in `ArtifactRef.export_error` instead; the durable
artifact is unaffected and can be exported later with
`seed_media_export_artifact`.

## Seedance Task States

| Status | Meaning |
|---|---|
| `queued` | Task is waiting to start |
| `running` | Task is generating |
| `succeeded` | Task completed, video available |
| `failed` | Task failed, check `error` field |
| `expired` | Task expired before completion |
| `cancelled` | Task was cancelled via DELETE |

### Why is there no queue position or ETA?

ModelArk publishes no queue position or ETA for Seedance tasks. The `queue`
field on `seedance_get_task`, `seedance_get_tasks`, and `seedance_list_tasks`
is derived by the server from the task's own timestamps: time queued, time
running, service tier (`flex` usually queues longer), and `expires_at`, the
time the provider fails an unfinished task (`created_at` +
`execution_expires_after`; `expires_at_estimated=true` means the 48-hour
default was assumed). Relay `queue.hint` to the user, and use
`seedance_get_tasks` to poll many tasks until `all_terminal` is `true`.

### Cannot Cancel or Delete

- `running` tasks cannot be cancelled or deleted — wait for completion
- `cancelled` tasks cannot be deleted

The `seedance_cancel_or_delete_task` tool requires `expected_status` to
match the actual status, preventing accidental destructive actions.

## Timeout on Long Generations

Seedream Pro and long Seed Audio generation can take 1-5 minutes. The
default request timeout is 10 minutes (`BYTEPLUS_REQUEST_TIMEOUT_MS=600000`).

If you experience timeouts:

1. Increase `BYTEPLUS_REQUEST_TIMEOUT_MS` (or `SEED_UNDERSTANDING_TIMEOUT_MS`
   for `seed_understand` only)
2. Check your network connectivity to the BytePlus region
3. Check `retryable` and `ambiguous_completion` on the `TIMEOUT` error. What a
   timeout means depends on the call:

| Call | `retryable` | `ambiguous_completion` | Server retries? | What to do |
|---|---|---|---|---|
| Create / generate / cancel / delete / MediaKit submit | `false` | `true` | No | It may have succeeded upstream. Do not retry blindly; reconcile by task ID, request ID, or `client_token` |
| Task polls (Seedance, Seed 3D, ASR query, MediaKit get) | `true` | `false` | Yes | Safe to poll again |
| `seed_understand` chat completion | `true` | `false` | No | Safe to retry, but a retry is a new billed completion; consider a longer `SEED_UNDERSTANDING_TIMEOUT_MS` or lower `reasoning_effort` |
| Variation batch deadline | see `error.phase` | | No | `queued` (`QUEUE_TIMEOUT`) is safe to retry; `generating` is ambiguous |

Budget reservations are committed for every `TIMEOUT`, since a timed-out
call may still have been billed.

## `seed_understand` Result Too Large or Missing Reasoning

The reasoning trace is no longer returned: deep thinking is always on, but
`choices[].reasoning_content` was removed and `thinking` is ignored. Only the
final answer comes back; `usage.reasoning_tokens` reports thinking cost. If
the answer itself is too large for the client context:

- Set `save_to` to an absolute path (stdio, inside an output root) and
  `return_content="none"` or `"summary"` (first 2,000 characters). The full
  answer is written to disk; `saved_path` and `content_chars` tell you where
  and how long.
- With a JSON `response_format`, `choices[].parsed` is returned even when
  `return_content` is `none`.
- If the answer ends with `finish_reason="length"`, raise `max_tokens`
  (it includes thinking tokens). `json_retry` does not retry truncated
  answers.

## Client Timeouts vs Provider Latency

A client-side MCP timeout is distinct from a server failure. Seedream Pro and
Seedance provider submissions can take 60–150 s (longer under cold start or
provider queue), so the server requires background execution for those
operations rather than keeping the original foreground request open.

- Submit `seedance_create_task`, `seedream_generate_image`, and
  `seed_audio_generate` through the `ark_job_*` workflow below. Its terminal
  result contains the typed tool output.
- On a task-capable client, you may instead invoke those tools with MCP task
  metadata, retain the returned MCP task ID, and poll `tasks/get` at the
  advertised interval until terminal.
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

### Background job is not available

`Background job is not available in this server instance` (local auth) or
`Background job is not available to this principal` (JWT auth) means the server
answering the request cannot resolve the job ID. In local auth mode the caller is
always the `local` principal, so the cause is process locality, not permissions:

- The job was submitted by a **different** `ark-mcp` process. Each `stdio`
  client starts its own process; run one shared loopback HTTP server
  (`make shared-http`) and point all clients at it.
- The server restarted: the default in-memory Docket backend discards active and
  retained jobs. Redis retention is the durability path.
- The job ID is wrong, or its retained result expired.

In JWT mode the same message also covers a genuine cross-principal or
cross-tenant job ID. The server deliberately does not distinguish missing,
expired, and not-owned IDs, so do not use the error to probe for job existence.

## Artifacts Not Persisting

Check:

1. `ARTIFACT_DIR` is writable
2. Disk has sufficient space
3. `ARTIFACT_BACKEND` is `filesystem`, or `object_storage` with working
   TOS/S3 credentials

A storage failure no longer fails the tool call; look for
`persistence_error` on the returned artifact (see above).

Run `make check-env` to validate configuration.

## HTTP Server Refuses to Start

A non-loopback HTTP bind is rejected in `local` auth mode. Set
`MCP_AUTH_MODE=jwt` and configure `MCP_JWT_JWKS_URI`, `MCP_JWT_ISSUER`, and
`MCP_JWT_AUDIENCE`. Add the public hostname to `MCP_ALLOWED_HOSTS` and browser
origins to `MCP_ALLOWED_ORIGINS`.

## Unknown Tool over HTTP

FastMCP hides tools whose required scopes are absent. Verify the Bearer token
has the appropriate `seed:audio:generate`, `seedream:generate`,
`seedance:create`, `seedance:read`, `seedance:delete`, `media:upload`, or
`artifacts:read` scope and contains both a principal (`sub`) and configured tenant claim.

## Running Tests

```bash
make test          # run all tests
make lint          # ruff check
make typecheck     # mypy
```

Tests use `respx` and an autouse socket guard—real network calls fail the suite.
