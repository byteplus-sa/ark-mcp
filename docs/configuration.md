# Configuration

Configuration is loaded from environment variables or `.env` by Pydantic
Settings. Copy `.env.example` to `.env`. Empty values are ignored.

`.env` is resolved **relative to the server's working directory**. Most MCP
clients set that correctly from their server configuration, so this is rarely
the problem. When a client cannot, it finds no `.env`, and the server starts
with no provider credentials and registers only the always-on job and artifact
tools — set `ARK_MCP_ENV_FILE` to an absolute path for that case, or launch the
server with its working directory at the repository root (for example
`uv --directory /path/to/ark-mcp run python -m ark_mcp`). The server logs a
`no_provider_credentials_configured` warning at startup when it happens; if a
client is missing tools and that warning is absent, look at
[troubleshooting](troubleshooting.md) instead.

| Variable | Default | Purpose |
|---|---|---|
| `ARK_MCP_ENV_FILE` | `.env` | Absolute path to the env file, for clients that cannot control the working directory |

## Providers and models

| Variable | Default | Purpose |
|---|---|---|
| `BYTEPLUS_MODELARK_API_KEY` | empty | Enables Seedream, Seedance, and Seed 2.1 understanding; sent as Bearer auth |
| `BYTEPLUS_SEED_SPEECH_API_KEY` | empty | Enables Seed Audio and speech-to-text; sent as `X-Api-Key` |
| `BYTEPLUS_VOD_MEDIAKIT_API_KEY` | empty | Enables MediaKit enhancement, transcode, subtitle burn-in/removal, audio-separation, and poll tools; sent as Bearer auth |
| `BYTEPLUS_MODELARK_BASE_URL` | AP Southeast ModelArk URL | HTTPS data-plane base URL |
| `BYTEPLUS_SEED_AUDIO_BASE_URL` | AP Southeast Seed Speech URL | HTTPS service base URL |
| `BYTEPLUS_VOD_MEDIAKIT_BASE_URL` | `https://mediakit.ap-southeast-1.bytepluses.com/api/v1` | HTTPS VOD AI MediaKit convenience-endpoint base URL |
| `SEEDREAM_DEFAULT_MODEL` | `dola-seedream-5-0-pro-260628` | Default image model/endpoint ID |
| `SEEDREAM_PROMPT_OPTIMIZATION_MODE` | `standard` | Mode 5.0 Pro sends when a caller omits `prompt_optimization`: `standard` (prompt-following quality) or `fast` (lower latency). Both are provider-accepted; an explicit per-call value wins |
| `SEEDANCE_DEFAULT_MODEL` | `dreamina-seedance-2-0-260128` | Default video model/endpoint ID |
| `SEED_UNDERSTANDING_DEFAULT_MODEL` | `dola-seed-2-1-turbo-260628` | Default understanding model/endpoint ID. Set to `dola-seed-evolving` to use the latest Pro-tier model (auto-resolves to family `pro`, no `SEED_UNDERSTANDING_MODEL_FAMILY` needed) |
| `SEEDREAM_MODEL_FAMILY` | empty | Family for a custom default: `pro`, `lite`, or `4x` |
| `SEEDANCE_MODEL_FAMILY` | empty | Family for a custom default: `standard`, `fast`, `mini`, `seedance_2_5`, or `seedance_2_5_premium` |
| `SEED_UNDERSTANDING_MODEL_FAMILY` | empty | Family for a custom default: `pro` or `turbo` |
| `SEEDREAM_MODEL_BINDINGS` | empty | JSON list of `{model_id, family}` bindings |
| `SEEDANCE_MODEL_BINDINGS` | empty | JSON list of `{model_id, family}` bindings |
| `SEED_UNDERSTANDING_MODEL_BINDINGS` | empty | JSON list of `{model_id, family}` bindings |
| `SEED_AUDIO_UNDERSTANDING_MODEL` | `seed-2-0-lite-260428` | Model/endpoint ID used by `seed_audio_understand`. Any ModelArk model that accepts Chat Completions `input_audio` parts with deep thinking works; change it when a newer audio-capable model ships |
| `BYTEPLUS_MODELARK_SEEDANCE_2_5_PREMIUM_ENABLED` | `false` | Feature flag for whitelist-only Seedance 2.5 Premium (4K). Registers `seedance_2_5_premium_create_task` and its variations tool; reuses the ModelArk key. When false, `seedance_2_5_premium` bindings are rejected at startup |
| `SEEDANCE_2_5_PREMIUM_MODEL` | `dreamina-seedance-2-5-premium-260915` | Premium model/endpoint ID bound automatically when the flag is on and `SEEDANCE_MODEL_BINDINGS` has no `seedance_2_5_premium` binding |
| `BYTEPLUS_MODELARK_3D_ENABLED` | `false` | Feature flag for 3D generation (Hyper3D + Hitem3d); reuses the ModelArk key, disabled by default |
| `HYPER3D_DEFAULT_MODEL` | `hyper3d-gen2` | Default Hyper3D model/endpoint ID |
| `HITEM3D_DEFAULT_MODEL` | `hitem3d-2-0` | Default Hitem3d model/endpoint ID |
| `SEED3D_MODEL_BINDINGS` | empty | JSON list of `{model_id, family}` bindings (`hyper3d` or `hitem3d`) |

The two built-in default IDs have known families. A custom ID must be bound
explicitly; the server does not infer capabilities from substrings in an ID.
For example:

```dotenv
SEEDREAM_DEFAULT_MODEL=my-image-endpoint
SEEDREAM_MODEL_BINDINGS=[{"model_id":"my-image-endpoint","family":"pro"}]
```

Credentials are startup-only. If a provider key is absent, its tools are not
registered. Specifically, `vod_enhance_video` is registered independently
when `BYTEPLUS_VOD_MEDIAKIT_API_KEY` is non-empty; it does not require the
ModelArk key. Provider base URLs must use HTTPS, include a hostname, and must
not contain embedded credentials.

## Transport and authentication

| Variable | Default | Purpose |
|---|---|---|
| `MCP_TRANSPORT` | `stdio` | `stdio` or Streamable `http` |
| `MCP_HOST` | `127.0.0.1` | HTTP bind address |
| `MCP_PORT` | `3000` | HTTP listen port |
| `MCP_ALLOWED_HOSTS` | loopback hosts | Comma-separated accepted Host headers |
| `MCP_ALLOWED_ORIGINS` | empty | Comma-separated accepted browser Origins |
| `MCP_HTTP_MAX_BODY_BYTES` | `314572800` (300 MiB) | Maximum HTTP request body; sized to inline the largest supported Base64 media upload (200 MiB video inflates ~4/3x). A smaller value logs a warning at startup (large inlined uploads will 413). |
| `READINESS_CHECK_PROVIDERS` | `false` | When true, `/ready` also checks provider connectivity |
| `READINESS_PROVIDER_TIMEOUT_SECONDS` | `2.0` | Per-provider timeout for readiness checks |
| `RATE_LIMIT_RPM` | `0` | Max HTTP requests per minute per client IP; 0 disables |
| `RATE_LIMIT_BURST` | `0` | Token bucket burst size; 0 defaults to `RATE_LIMIT_RPM` |
| `RATE_LIMIT_TRUST_PROXY_HEADERS` | `false` | Trust the first `X-Forwarded-For` entry for rate-limit keys; enable only behind a trusted proxy |
| `MCP_AUTH_MODE` | `local` | `local` or `jwt` |
| `MCP_JWT_JWKS_URI` | empty | HTTPS JWKS endpoint for JWT verification |
| `MCP_JWT_ISSUER` | empty | Required token issuer |
| `MCP_JWT_AUDIENCE` | empty | Required token audience |
| `MCP_TENANT_CLAIM` | `tenant_id` | Claim used for tenant isolation |
| `MCP_JWT_CLOCK_SKEW_SECONDS` | `30` | Tolerated clock skew (seconds) for JWT `nbf` (not-before) validation |
| `MCP_JWT_PROVIDE_DISCOVERY` | `false` | When true in JWT mode, serve RFC 9728 OAuth Protected Resource Metadata so MCP clients can discover the authorization server |
| `MCP_PUBLIC_BASE_URL` | empty | Public HTTPS base URL of this server; required when `MCP_JWT_PROVIDE_DISCOVERY=true` |
| `MCP_JWT_SCOPES_SUPPORTED` | empty | Comma-separated scopes advertised in Protected Resource Metadata |

`FASTMCP_TRANSPORT`, `FASTMCP_HOST`, and `FASTMCP_PORT` are accepted as
aliases for the corresponding `MCP_*` transport settings.

`local` auth is accepted only for stdio or loopback HTTP. Binding HTTP to a
non-loopback address fails closed unless JWT mode and all verifier settings are
present. JWT tokens must contain a principal (`sub`) and the configured tenant
claim. Tool scopes are enforced by FastMCP:

- `seed:audio:generate`
- `seedream:generate`
- `seedance:create`, `seedance:read`, `seedance:delete`
- `understanding:read`
- `vod:enhance`
- `vod:transcode`
- `vod:subtitle:add`
- `vod:subtitle:remove`
- `vod:read`
- `vod:extract`
- `media:upload`
- `media:presign`
- `artifacts:read`

## Seed Speech ASR (STT)

The `speech_to_text` tool is registered when `BYTEPLUS_SEED_SPEECH_API_KEY` is
set — the same key that enables Seed Audio (TTS). It submits audio via HTTP,
polls until transcription is complete, and returns the complete
`TranscriptionResult` through a required background job, submitted through the
ordinary `ark_job_*` tools or native MCP task augmentation on a task-capable
client. Audio input accepts
URL, Base64, or a local file path (stdio only).

| Variable | Default | Purpose |
|---|---|---|
| `BYTEPLUS_SEED_SPEECH_API_KEY` | empty | Enables Seed Audio + speech-to-text; sent as `X-Api-Key` header |
| `SEED_SPEECH_ASR_BASE_URL` | `https://voice.ap-southeast-1.bytepluses.com` | Seed Speech ASR HTTP host |
| `SEED_SPEECH_ASR_POLL_INTERVAL_SECONDS` | `3.0` | Seconds between ASR query polls |
| `SEED_SPEECH_ASR_POLL_MAX_SECONDS` | `600.0` | Maximum total seconds to wait for ASR result |

JWT tool scope for speech-to-text:

- `seed:asr:transcribe`

## VOD AI MediaKit

`vod_enhance_video` is registered when `BYTEPLUS_VOD_MEDIAKIT_API_KEY` is
set. The initial tool intentionally exposes only the exact
`common`/`professional`/`4k`/`high`/24-fps profile and serializes the project
label upstream as case-sensitive `Project`. Submission returns an asynchronous
task ID for `vod_get_enhancement_task`; the poll tool returns and best-effort
persists completed outputs. The submit POST is not retried automatically.
Convenience-endpoint pricing is not yet confirmed, so the tool does not emit a
cost estimate.

`vod_transcode_video` and `vod_get_transcode_task` are also registered when
`BYTEPLUS_VOD_MEDIAKIT_API_KEY` is set. `vod_transcode_video` submits an async
transcoding task (codec, container format, scaling, bitrate, frame rate, HDR);
`vod_get_transcode_task` polls it and best-effort persists the completed output.
The transcode request/status contract is verified from the official AI MediaKit
API reference; the output URL hostname (`*.byteplusvod.com`) is confirmed and
trusted for durable persistence. `queue_id`/`Project` request params remain
unverified and are not exposed.

`vod_add_subtitles` / `vod_get_subtitle_addition_task` and
`vod_remove_subtitles` / `vod_get_subtitle_removal_task` use the same API key.
Addition accepts a public HTTPS SRT, VTT, or ASS file or inline timed cues and
burns them into the output video. Removal defaults to dialogue-subtitle mode;
the broader `text` mode may also erase titles, labels, or watermarks. Both
submit asynchronously, use `client_token` to reconcile ambiguous submissions,
and preserve the provider URL when optional durable persistence is skipped or
fails. The optional `project` and removal `model_version` fields are legacy
convenience-endpoint extensions and are omitted unless explicitly supplied.

## VOD AI MediaKit audio separation

`vod_separate_audio` and `vod_get_audio_separation` are registered when
`BYTEPLUS_VOD_MEDIAKIT_API_KEY` is set, sharing the same Bearer-authenticated
convenience surface as enhancement and transcoding.

`vod_separate_audio` submits a `POST /api/v1/tools/separate-voice` task from a
public HTTPS `audio_url` or `video_url` (exactly one) plus an optional `scene`
(`Audio` default, `Music`, `Drama`, `Narrate`) and `output_format` (`aac`
default, `mp3`, `wav`, `m4a`, `flac`). `vod_get_audio_separation` polls
`GET /api/v1/tasks/{task_id}` and returns each separated track's expiring
`source_url` (valid 24 hours) plus a durable `artifact` reference when
best-effort persistence succeeds.

## Private asset library (AK/SK)

The `ark_asset_*` tools are registered only when both
`BYTEPLUS_MODELARK_ACCESS_KEY` and `BYTEPLUS_MODELARK_SECRET_KEY` are set; they
do not need `BYTEPLUS_MODELARK_API_KEY`. Seedance `asset://` references need
only the API key. See [assets.md](assets.md).

| Variable | Default | Description |
|---|---|---|
| `BYTEPLUS_MODELARK_ACCESS_KEY` | empty | BytePlus IAM access key (`AKLT…` long-term or `AKTP…` STS) for signed ModelArk OpenAPI calls |
| `BYTEPLUS_MODELARK_SECRET_KEY` | empty | Matching secret key; must be set together with the access key |
| `BYTEPLUS_MODELARK_SESSION_TOKEN` | empty | STS session token, required for `AKTP…` keys (sent as signed `X-Security-Token`) |
| `BYTEPLUS_MODELARK_OPENAPI_BASE_URL` | `https://ark.ap-southeast-1.byteplusapi.com` | ModelArk OpenAPI host (HTTPS) |
| `BYTEPLUS_MODELARK_REGION` | `ap-southeast-1` | Signing region |
| `BYTEPLUS_MODELARK_PROJECT_NAME` | `default` | Default project for groups and assets; must match the inference endpoint's project |
| `BYTEPLUS_MODELARK_ASSETS_ALLOW_DELETE` | `false` | Registers the irreversible `ark_asset_delete` / `ark_asset_group_delete` tools |
| `BYTEPLUS_MODELARK_ASSET_CREATE_QPM` | `3` | Client-side `CreateAsset` pacing; set to your tier (3 Entry, 120 Advanced, 300 Premium) |
| `BYTEPLUS_MODELARK_ASSET_VERIFY_CALLBACK_URL` | empty | Default HTTPS redirect for real-person verification |
| `BYTEPLUS_MODELARK_ASSET_REFERENCE_MODE` | `off` | Seedream / Seed Audio `asset://` handling: `off` rejects, `resolve` swaps in the temporary asset URL (experimental; needs AK/SK) |
| `SEEDANCE_ASSET_PREFLIGHT` | `true` | With AK/SK, check `asset://` references with `GetAsset` before a billed Seedance submit |

JWT tool scopes: `assets:read`, `assets:write`, `assets:verify`, `assets:delete`.

## Object storage (TOS or S3, optional)

The `media_upload`, `media_upload_batch`, `media_presign`, and
`media_presign_batch` tools are registered when the selected object-storage
backend is configured. `media_upload` uploads media to a **private** bucket and
returns a presigned HTTPS GET URL; `media_upload_batch` does the same for 1-50
files in one call; `media_presign` generates a fresh presigned URL for an
existing object without re-uploading.
Use `OBJECT_STORAGE_BACKEND` to select `tos` (default) or `s3`.

| Variable | Default | Purpose |
|---|---|---|
| `MEDIA_UPLOAD_BATCH_MAX_BYTES` | `524288000` (500 MiB) | Maximum total decoded bytes in one `media_upload_batch` call; checked before any upload starts |

### TOS backend

| Variable | Default | Purpose |
|---|---|---|
| `TOS_ACCESS_KEY` | empty | TOS access key (AK) |
| `TOS_SECRET_KEY` | empty | TOS secret key (SK) |
| `TOS_SECURITY_TOKEN` | empty | Optional temporary security token |
| `TOS_BUCKET` | empty | Target bucket name |
| `TOS_REGION` | `ap-southeast-1` | TOS region |
| `TOS_ENDPOINT` | `tos-ap-southeast-1.bytepluses.com` | TOS API endpoint |
| `TOS_PRESIGN_TTL_SECONDS` | `1800` | Presigned URL validity in seconds (60–604800) |

### S3 backend

| Variable | Default | Purpose |
|---|---|---|
| `S3_ACCESS_KEY` | empty | S3 access key ID |
| `S3_SECRET_KEY` | empty | S3 secret access key |
| `S3_BUCKET` | empty | Target bucket name |
| `S3_REGION` | `us-east-1` | AWS region |
| `S3_ENDPOINT` | empty | Custom endpoint for S3-compatible storage (MinIO, R2) |
| `S3_PRESIGN_TTL_SECONDS` | `1800` | Presigned URL validity in seconds (60–604800) |
| `OBJECT_STORAGE_BACKEND` | `tos` | Select active backend: `tos` or `s3` |

AK and SK must both be set or both be empty. The bucket must remain private;
presigned URLs grant temporary read access to individual objects. When
`S3_ENDPOINT` is set, path-style addressing is used automatically for
S3-compatible storage.

## Persistence and runtime policy

| Variable | Default | Purpose |
|---|---|---|
| `ARTIFACT_BACKEND` | `filesystem` | `filesystem` (local disk) or `object_storage` (TOS/S3) |
| `STATE_BACKEND` | `sqlite` | Task ownership/budget/cache backend; only `sqlite` (single instance) is implemented |
| `ARTIFACT_DIR` | `~/.ark-mcp/artifacts` | Media, metadata, ownership, and budget state |
| `ARTIFACT_TTL_SECONDS` | `604800` | Artifact retention, in seconds |
| `ARTIFACT_SWEEP_INTERVAL_SECONDS` | `3600` | Interval between background artifact/state expiry sweeps |
| `STATE_PRUNE_MAX_AGE_DAYS` | `30` | Max age for ownership/budget/cache rows before pruning |
| `MCP_INLINE_MEDIA_MAX_BYTES` | `8388608` | Maximum inline MCP media size |
| `ARTIFACT_DOWNLOAD_TIMEOUT_SECONDS` | `120` | Timeout for each attempt to download provider output into artifact storage (filesystem and object-storage backends) |
| `ARTIFACT_DOWNLOAD_MAX_ATTEMPTS` | `3` | Attempts (1-10) for a retryable output download (timeouts, network errors, 408/429/5xx), with 1s, 2s, 4s, ... backoff. Expired, untrusted, or oversized sources are never retried |
| `ARTIFACT_INLINE_FALLBACK_MAX_BYTES` | `8388608` (8 MiB) | When inline Base64 output (Seed Audio, Seedream `b64_json`) cannot be stored, return it inline in `ArtifactRef.fallback_data` up to this decoded size; `0` disables |
| `PROVIDER_MAX_CONCURRENCY` | `5` | Process-wide slots per provider |
| `PRINCIPAL_MAX_CONCURRENCY` | `3` | Shared slots per authenticated principal |
| `DAILY_BUDGET_USD` | `0` | Per-principal UTC daily estimate limit; zero records only |
| `PERSISTENCE_CACHE_MAX_SIZE` | `10000` | Max cached provider task IDs in artifact-resolution cache |
| `PERSISTENCE_CACHE_TTL_SECONDS` | `86400` | TTL for cached task-to-artifact mappings (seconds) |

A billed output is never dropped because storage failed; see
[Artifacts](artifacts.md#persistence-failures).

The filesystem backend enforces principal and tenant ownership. It is suitable
for one process. Multiple replicas require shared artifact, task-ownership,
budget, cache, and limiter implementations before horizontal scaling is safe.

## Local output paths

| Variable | Default | Purpose |
|---|---|---|
| `OUTPUT_ROOTS` | empty | Comma-separated absolute directories where stdio tools may write files (`output_path`, `output_dir`, `save_to`, `destination_path`) |

The client's MCP roots (`roots/list`) take precedence when the client provides
them; `OUTPUT_ROOTS` is the fallback. With neither, local path writing is
disabled and those fields are rejected. Path writing is stdio-only. See
[Security](security.md#local-output-paths).

## Timeouts and logging

| Variable | Default | Purpose |
|---|---|---|
| `BYTEPLUS_CONNECT_TIMEOUT_MS` | `10000` | Provider connection timeout |
| `BYTEPLUS_REQUEST_TIMEOUT_MS` | `600000` | Full provider request timeout |
| `SEED_UNDERSTANDING_TIMEOUT_MS` | unset (uses `BYTEPLUS_REQUEST_TIMEOUT_MS`) | Request timeout for `seed_understand` and `seed_audio_understand` chat completions. Deep thinking is always on, so long analyses may need more than the general request timeout |
| `FASTMCP_DOCKET_URL` | `memory://` | FastMCP background-task backend used by generation, transcription, upload, provider submission, understanding, and optional media-persistence calls; Redis can retain task state across restarts of the same single-replica deployment; it does not make SQLite state horizontally scalable |
| `FASTMCP_TASKS_ENCRYPTION_KEY` | unset | Required for JWT-authenticated Redis task backends; use at least 32 random characters and keep the same key across restarts |
| `FASTMCP_DOCKET_CONCURRENCY` | `10` | Maximum active background tasks per worker; provider/principal limits still apply |
| `ARK_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR` |

Logs are structured JSON on stderr. Provider credentials and sensitive media
fields are redacted. The default in-memory task backend is process-local; a
server restart discards active and retained task state.


### Background task ownership and recovery

**Keep the runtime database with the queue.** MCP task ownership is persisted in
`runtime.sqlite3` using the configured tenant claim and principal. Every
`tasks/get`, `tasks/update`, and `tasks/cancel` request checks that owner.
Missing identity or ownership fails closed in JWT mode.

One shared server process gives all of its clients one queue: in local auth
mode every loopback client is the same `local`/`local` principal, so a job
submitted by one client is visible to the others. This remains single-instance
state; see [transports.md](transports.md).

Before a required-task handler starts, it atomically records an execution claim
in the same database. If that MCP task is delivered again, the handler returns
an error without repeating provider work. This favors avoiding duplicate charges
over automatic recovery: after an interrupted submission, reconcile its provider
outcome before creating a new operation. Optional output-retrieval tasks can be
retried because they do not submit paid generation work.

The default in-memory queue still loses task results at process exit. With Redis,
retain the runtime database, backend, and encryption key together, and run only
one application replica. Snapshot encryption protects caller credentials and
HTTP headers stored by FastMCP; Redis must also restrict access to task arguments
and results, which may contain media URLs and prompts. `rediss://` protects the
connection and does not replace snapshot encryption. A wrong or missing key
cannot decrypt existing encrypted snapshots. See [deployment.md](deployment.md).
