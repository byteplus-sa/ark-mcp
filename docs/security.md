# Security

This document consolidates the server's security model. See
[configuration.md](configuration.md) for the full env-var reference,
[transports.md](transports.md) for transport setup, and
[deployment.md](deployment.md) for remote-deployment hardening.

## Auth modes

`AuthMode` (`config/env.py`) is a `StrEnum`: `LOCAL = "local"` and
`JWT = "jwt"`.

| Mode | Transport | Behavior |
|---|---|---|
| `local` (default) | `stdio` or loopback HTTP | No FastMCP auth provider; a single trusted principal `local`. |
| `jwt` | HTTP (required for non-loopback) | `fastmcp.server.auth.JWTVerifier` with `ssrf_safe=True`. |

`build_auth_provider` returns `None` for `LOCAL`, and a `JWTVerifier` for
`JWT`. **Fail-closed is structural and startup-time:** if
`MCP_TRANSPORT=http` + `MCP_AUTH_MODE=local` + `MCP_HOST` is not in
`{127.0.0.1, ::1, localhost}`, `Settings` construction raises
`ValueError("HTTP on a non-loopback host requires MCP_AUTH_MODE=jwt.")` and
the server never starts.

## JWT verification

The verifier is constructed with these settings (all required in JWT mode):

| Field | Env var | Constraint |
|---|---|---|
| `jwks_uri` | `MCP_JWT_JWKS_URI` | must be `https://` with a hostname |
| `issuer` | `MCP_JWT_ISSUER` | non-empty |
| `audience` | `MCP_JWT_AUDIENCE` | non-empty |
| `clock_skew_seconds` | `MCP_JWT_CLOCK_SKEW_SECONDS` | `0..300` (default `30`) |
| `ssrf_safe` | — | hard-coded `True` |

The verifier is `StrictJWTVerifier`, pinned to RS256. Tokens missing an `exp`
claim are rejected, and a token whose `nbf` claim is still in the future
(beyond the configured clock skew) is rejected.

### OAuth discovery (optional)

By default JWT mode is a bare verifier with no OAuth discovery — suitable for
internal machine-to-machine clients that already know how to obtain tokens.
Set `MCP_JWT_PROVIDE_DISCOVERY=true` (plus `MCP_PUBLIC_BASE_URL`) to wrap the
verifier in FastMCP's `RemoteAuthProvider`, which serves RFC 9728 OAuth
Protected Resource Metadata at `/.well-known/oauth-protected-resource` and
advertises the scopes in `MCP_JWT_SCOPES_SUPPORTED`. This makes the server
discoverable by spec-compliant MCP clients. The bare-verifier mode is
unchanged when the flag is off.

### Principal and tenant extraction

`get_principal(ctx)` (`runtime.py`) resolves the principal identity from the
verified token:

- **`sub` claim → `token.subject` → `token.client_id`** (first truthy `str`
  wins). Missing → `PermissionError("The access token is missing a principal identity.")`.
- **Tenant** is read from the claim named by `MCP_TENANT_CLAIM` (default
  `"tenant_id"`). Missing/non-string → `PermissionError`.
- A missing token → `PermissionError("An authenticated access token is required.")`.

There is **no anonymous remote principal** — JWT mode fails closed. The only
way to get an `is_local` principal (`principal_id == "local"` with
`transport == "stdio"`) is `LOCAL` mode, which returns `PrincipalContext()`
untouched (`local`/`local`, empty scopes, `transport="stdio"`). This holds for
loopback HTTP too, and is intentional: it is what lets all clients of one shared
loopback server see each other's background jobs.

The trust implication is explicit: with `MCP_AUTH_MODE=local` over loopback,
**any local process or user that can reach the port acts as the `local`
principal** — including billable tool calls and artifact or job reads. This
matches the `stdio` trust model, but the port is reachable by every local
account, so bind loopback only and do not expose it through a proxy.

### `PrincipalContext` / `AuthContext`

A frozen Pydantic model (`security/auth_context.py`):

| Field | Type | Default |
|---|---|---|
| `principal_id` | `str` | `"local"` |
| `tenant_id` | `str` | `"local"` |
| `scopes` | `frozenset[str]` | `frozenset()` |
| `transport` | `Literal["stdio", "http"]` | `"stdio"` |

`is_local` returns `principal_id == "local" and transport == "stdio"`.
`AuthContext` is a compatibility alias for `PrincipalContext`.

## Scope taxonomy

`component_auth(settings, *scopes)` returns `None` in `LOCAL` mode, or
`require_scopes(*scopes)` (a FastMCP `AuthCheck`) in JWT mode. The
tool → scope mapping is wired in `server.py::register_tools`:

| Scope | Protects |
|---|---|
| `seed:audio:generate` | `seed_audio_generate`, `seed_audio_generate_variations` |
| `seedream:generate` | `seedream_generate_image`, `seedream_generate_image_variations` |
| `seedance:create` | `seedance_create_task`, `seedance_create_task_variations` |
| `seedance:read` | `seedance_get_task`, `seedance_get_tasks`, `seedance_list_tasks` |
| `seedance:delete` | `seedance_cancel_or_delete_task` |
| `seed:asr:transcribe` | `speech_to_text` |
| `vod:enhance` | `vod_enhance_video` |
| `vod:transcode` | `vod_transcode_video` |
| `vod:subtitle:add` | `vod_add_subtitles` |
| `vod:subtitle:remove` | `vod_remove_subtitles` |
| `vod:read` | All MediaKit task polling tools |
| `vod:extract` | `vod_separate_audio` |
| `media:upload` | `media_upload`, `media_upload_batch`, `seed_media_persist_url` |
| `media:presign` | `media_presign`, `media_presign_batch` |
| `artifacts:read` | MCP resource `seed-media://artifacts/{artifact_id}`, `seed_media_get_artifact`, `seed_media_export_artifact` |
| `assets:read` | `ark_asset_group_get`, `ark_asset_group_list`, `ark_asset_get`, `ark_asset_list` |
| `assets:write` | `ark_asset_group_ensure`, `ark_asset_group_create`, `ark_asset_group_update`, `ark_asset_create`, `ark_asset_update` |
| `assets:verify` | `ark_asset_verification_start`, `ark_asset_verification_result` |
| `assets:delete` | `ark_asset_delete`, `ark_asset_group_delete` (registered only with `BYTEPLUS_MODELARK_ASSETS_ALLOW_DELETE=true`) |

The `seed-health://status` resource and the `/health`, `/ready`, `/metrics`
routes are **not** scope-protected at the FastMCP layer. Seed Audio and
speech-to-text tools are registered only when `BYTEPLUS_SEED_SPEECH_API_KEY`
is set; Seedream/Seedance tools only when `BYTEPLUS_MODELARK_API_KEY` is set;
`vod_enhance_video`, `vod_get_enhancement_task`,
`vod_transcode_video`, `vod_get_transcode_task`, `vod_add_subtitles`,
`vod_get_subtitle_addition_task`, `vod_remove_subtitles`,
`vod_get_subtitle_removal_task`, `vod_separate_audio`, and
`vod_get_audio_separation` only when
`BYTEPLUS_VOD_MEDIAKIT_API_KEY` is set. The `media_upload`,
`media_upload_batch`, `media_presign`, and `media_presign_batch` tools are
registered only when object storage credentials are set (TOS:
`TOS_ACCESS_KEY` / `TOS_SECRET_KEY` / `TOS_BUCKET`, or S3:
`S3_ACCESS_KEY` / `S3_SECRET_KEY` / `S3_BUCKET` with
`OBJECT_STORAGE_BACKEND=s3`).

### Asset library credentials (AK/SK)

`BYTEPLUS_MODELARK_ACCESS_KEY` / `_SECRET_KEY` (plus `_SESSION_TOKEN` for STS
keys) sign ModelArk OpenAPI requests with BytePlus V4 HMAC-SHA256
(`providers/byteplus_openapi/signing.py`). They are startup configuration
only and are never logged, echoed, or accepted as tool arguments. The
`ark_asset_*` tools are registered only when both keys are set. Asset
management acts on the whole BytePlus account rather than per MCP principal,
so grant `assets:*` scopes only to trusted callers. The real-person
verification `h5_link` embeds temporary credentials: it is returned to the
caller but never logged. `asset://<asset_id>` references are validated by
shape and never fetched by the server; `CreateAsset` source URLs pass the
standard URL policy. See [assets.md](assets.md).

### Object storage credentials

Object storage access key and secret key are loaded from environment
variables at startup (`TOS_ACCESS_KEY` / `TOS_SECRET_KEY` or
`S3_ACCESS_KEY` / `S3_SECRET_KEY`) and never accepted as tool arguments.
The bucket is **private** — presigned URLs grant temporary read access to
individual objects. Object keys are server-generated UUIDs under a
caller-supplied prefix; `key_prefix` is sanitized to alphanumeric, `-`, `_`,
and `/`. File-path input is restricted to the `stdio` transport to prevent
remote file reads over HTTP.

Uploaded object keys are recorded in the SQLite `object_key_ownership`
ledger keyed by `(principal_id, tenant_id)`. `media_presign` and
`media_presign_batch` verify the caller owns each key before minting a read
URL; a remote principal cannot re-presign another tenant's object. In `LOCAL`
mode, unrecorded keys remain presignable by the single local principal. A
successful `record` or `require_owner` refreshes the row's `created_at`
timestamp, so regularly presigned keys are not expired by the state sweeper.
Rows are pruned by the background state sweeper after
`STATE_PRUNE_MAX_AGE_DAYS` (default `30`) of inactivity; a long-lived key
that has not been presigned within that window must be re-uploaded before it
can be presigned again by a remote principal.

## Host / Origin protection

Enabled in `__main__.py` for HTTP transport via FastMCP-native guards
(`host_origin_protection=True` plus `allowed_hosts`/`allowed_origins`). This
is **not** a custom middleware in this repo.

| Env var | Default | Notes |
|---|---|---|
| `MCP_ALLOWED_HOSTS` | `127.0.0.1,localhost,[::1]` | comma-separated |
| `MCP_ALLOWED_ORIGINS` | `""` (empty → all browser origins rejected) | each must be an `http`/`https` URL with a hostname |

When deploying behind a public hostname, set **both** the public hostname
(in `MCP_ALLOWED_HOSTS`) and the allowed browser origins (in
`MCP_ALLOWED_ORIGINS`).

## Body-limit middleware (`security/http_middleware.py`)

`RequestBodyLimitMiddleware` (ASGI) — the only custom ASGI middleware
shipped in `security/`. Wired for HTTP transport only.

| Env var | Default | Notes |
|---|---|---|
| `MCP_HTTP_MAX_BODY_BYTES` | `314_572_800` (300 MiB), `ge=1` | rejects oversized bodies; sized to inline the largest supported Base64 media upload (200 MiB video inflates ~4/3x) |

Behavior: reads `Content-Length`; if `> max_bytes` → `413 "Request body too
large"`; on unparseable `Content-Length` → `400`. It also wraps `receive` to
enforce the limit on streamed bodies; if the response has already started,
it re-raises `RequestBodyTooLarge` (the connection errors out rather than
sending a clean 413 mid-stream).

## Rate limiting (`security/http_middleware.py`)

`RateLimitMiddleware` (ASGI) — a per-client-IP token bucket rate limiter,
wired for HTTP transport only when `RATE_LIMIT_RPM > 0`. Disabled by default.

| Env var | Default | Notes |
|---|---|---|
| `RATE_LIMIT_RPM` | `0` (disabled) | Maximum HTTP requests per minute per client IP |
| `RATE_LIMIT_BURST` | `0` (defaults to `RATE_LIMIT_RPM`) | Token bucket burst capacity |

Behavior: each client IP gets an in-memory token bucket. The refill rate is
`rpm / 60` tokens per second. Each request consumes one token. When the
bucket is empty, the middleware returns `429 "Rate limit exceeded"` with a
`Retry-After` header indicating the seconds until the next token is
available, plus `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and
`X-RateLimit-Reset` headers so well-behaved clients can self-throttle. The
bucket dict is protected by an `asyncio.Lock`.

## SSRF-safe downloader (`security/safe_downloader.py`)

`SafeDownloader` downloads provider media (for `copy_from_trusted_url`) with
two-layer SSRF defense. Constructor defaults: `timeout=120.0s`,
`connect_timeout=30.0s`, `max_attempts=3`, `follow_redirects=False`,
`trust_env=False` (ignores `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY`). The runtime
builds it from `ARTIFACT_DOWNLOAD_TIMEOUT_SECONDS`,
`BYTEPLUS_CONNECT_TIMEOUT_MS`, and `ARTIFACT_DOWNLOAD_MAX_ATTEMPTS`.

Retryable failures (timeouts, network errors, 408/429/5xx) are retried with
1s, 2s, 4s, ... backoff. Policy failures (untrusted host, redirect rejection,
oversize, expired source) are never retried, so retries cannot widen what the
downloader accepts.

`download(url, *, trusted_hosts, max_bytes, max_redirects=5)`:

1. `validate_url(current_url)` — full SSRF validation (scheme, host, DNS,
   IP-class denial) returns pinned public IPs.
2. If `not trusted_hosts(hostname)` → `ValueError` (an application host
   allowlist on top of the IP-level check).
3. `_request_pinned(validated, max_bytes=...)` — **IP pinning**: connects to
   `validated.addresses[0]` while preserving the original `Host` header and
   TLS SNI (`sni_hostname`), preventing DNS rebinding between validation and
   connection. The connect URL replaces the netloc with the IP literal
   (IPv6 bracketed).
4. Redirects are handled hop-by-hop: each `Location` is revalidated from
   scratch (DNS + IP + host policy all re-applied). Missing `Location` →
   `ValueError`; too many redirects → `ValueError`.
5. **Streaming size cap:** if `Content-Length > max_bytes` → `ValueError`;
   chunks are accumulated and checked against `max_bytes` after each
   `extend`.

`FilesystemArtifactStore` restricts `copy_from_trusted_url` to provider hosts
via suffix allowlist: `.bytepluses.com`, `.byteplus.com`, `.bytedance.com`,
`.bytednsdoc.com`, `.volces.com`, `.byteplusvod.com`,
`.tos-ap-southeast.bytepluses.com`.

`seed_media_persist_url` accepts a caller-supplied URL, so it relies on the
same controls: the URL goes through `copy_from_trusted_url` with this host
allowlist, full SSRF validation and IP pinning, per-hop redirect
re-validation, and the per-media-type size limits. Any host outside the
allowlist is rejected; the tool cannot be used to fetch arbitrary URLs. It
requires the `media:upload` scope in JWT mode.

For VOD AI MediaKit, video processing and artifact persistence are separate
outcomes. The tool always preserves a successful provider output URL for the
authorized caller, then best-effort downloads it through this SSRF-safe path.
Outputs above the 200 MiB video limit or failing host/MIME/download/storage
checks return `persistence="failed"` without changing provider success into a
provider failure. Full source URLs and credentials are excluded from logs and
safe error messages.

## Local output paths

Tools that write to a caller-chosen local path — `output_path`/`output_dir` on
generation and get tools, `save_to` on `seed_understand` and
`seed_audio_understand`, and
`destination_path` on `seed_media_export_artifact` — share one policy in
`security/output_paths.py`:

- **stdio only.** The client and server must share a filesystem; the fields
  are rejected on HTTP transport.
- **Allowed roots.** The client's MCP roots (`roots/list`, `file://` URIs
  only, 2-second timeout) are used when available; otherwise `OUTPUT_ROOTS`
  (comma-separated absolute directories). With neither, path writing is
  disabled and every such field is rejected.
- **Containment.** The path must be absolute and resolve inside a root. A file
  target cannot be a directory or a root itself.
- **Pre-flight.** Paths are validated before any billable provider call, so a
  rejected path never produces paid output.
- **TOCTOU-safe writes.** Missing parent directories are created one level at
  a time with containment re-checked. The final directory is opened with
  `O_NOFOLLOW`, its real location is re-checked against the roots, and the
  file is published atomically through the directory descriptor. A symlink
  swapped in after validation cannot redirect the write.
- **No silent overwrite.** Publishing is exclusive unless `overwrite=true`; a
  destination that already holds identical bytes (same SHA-256) is a success.

Breaking change for `seed_media_export_artifact`: `destination_path` must now
be absolute and inside an allowed root, and an existing file is no longer
silently overwritten (pass `overwrite=true`).

## URL policy (`security/url_policy.py`)

Exception: `UrlValidationError(ValueError)`.

- **Blocked hostnames (literal):** `169.254.169.254` (AWS metadata),
  `metadata.google.internal` (GCP), `100.100.100.200` (Alibaba),
  `fd00:ec2::254` (AWS IPv6 metadata).
- **Allowed schemes:** `https` always; `http` only when the caller passes
  `allow_http=True`. `file://` is never allowed.
- **No credentials in URLs** (`userinfo` rejected).
- **Hostname** is IDNA-encoded → ASCII → lowercased.
- **Ports:** only `443` (https) and `80` (http) are allowed; any other
  explicit port is rejected. Default is 443 (https) / 80 (http).
- **DNS + IP denial:** `resolve_public_addresses` resolves the hostname
  (or uses a literal IP directly), then denies any address that is not
  globally reachable — `is_private`, `is_loopback`, `is_link_local`,
  `is_multicast`, `is_reserved`, `is_unspecified`, or `not is_global` (which
  additionally covers CGNAT shared space `100.64.0.0/10`). For IPv6, embedded
  IPv4 transition formats (`ipv4_mapped`, `sixtofour`, `teredo[1]`) are
  recursively checked.

`validate_url` combines syntax validation + DNS resolution and returns a
`ValidatedUrl(url, parsed, hostname, port, addresses)`.

## Media policy (`security/media_policy.py`)

Exception: `MediaValidationError(ValueError)`.

`MediaLimits` (returned by `get_media_limits()`, no env override in this
module) cap **provider-bound** media:

| Limit | Default |
|---|---|
| `audio_max_bytes` | 10 MiB |
| `audio_max_seconds` | 30 |
| `image_max_bytes` | 10 MiB |
| `video_max_bytes` | 200 MiB |

Allowed MIME types:

| Category | MIME types |
|---|---|
| Audio | `audio/wav`, `audio/x-wav`, `audio/wave`, `audio/mpeg`, `audio/mp3`, `audio/pcm`, `audio/x-pcm`, `audio/ogg`, `audio/ogg;codecs=opus`, `audio/webm` |
| Image | `image/jpeg`, `image/jpg`, `image/png`, `image/webp` |
| Video | `video/mp4`, `video/quicktime` |

`check_base64_size` estimates decoded size as `(len(stripped) * 3) // 4`
(without full decode); `decode_base64_safely` validates then decodes.
`check_audio_duration_from_base64` decodes WAV (RIFF) headers and enforces
`audio_max_seconds` for Base64 audio references; non-WAV formats (MP3, PCM,
OGG) cannot be measured without a full decoder and are skipped — the
provider enforces the limit server-side for those. This preflight is
best-effort: it trusts the declared `data` chunk size, so a crafted header
with an under-reported size could bypass it. The provider enforces the
30-second limit server-side regardless.

> `MCP_INLINE_MEDIA_MAX_BYTES` (default 8 MiB) lives in `config/env.py`, not
> here. It caps **inline MCP media returned to the client**; the per-type
> `MediaLimits` above cap **provider-bound** media. The two are independent.

## TLS and provider URLs

- `truststore.inject_into_ssl()` runs at module import in `server.py` and
  `__main__.py`, so Python uses the macOS Keychain for TLS verification.
- Provider base URLs (`BYTEPLUS_MODELARK_BASE_URL`,
  `BYTEPLUS_SEED_AUDIO_BASE_URL`, `BYTEPLUS_VOD_MEDIAKIT_BASE_URL`) are
  validated at settings load: must be
  `https://` with a hostname and no embedded credentials; trailing slash
  stripped.
- VOD AI MediaKit requests are authenticated with
  `Authorization: Bearer ${BYTEPLUS_VOD_MEDIAKIT_API_KEY}`; the key is never
  logged, returned, or accepted as a tool argument.
- Inline `subtitle_text` is treated as sensitive content and is redacted from
  structured logs together with prompt, subtitle, media URL, and credential fields.

## Settings caching

`get_settings()` is `@lru_cache(maxsize=1)`; `refresh_settings()` clears the
cache. Any auth/host/scope-related env change requires a process restart or
an explicit `refresh_settings()` call to take effect.


## MCP background task boundaries

**MCP task results use the same tenant/principal boundary as provider tasks.**
`TenantTasksExtension` records task ownership under the `mcp` namespace in the
runtime ownership database and checks it before get, update, or cancel. JWT mode
requires the configured tenant claim before accepting a tool call. FastMCP's own
client/subject namespace remains an additional check, not the application's
source of tenant isolation. A token for a different tenant cannot retrieve a
cached media URL even when its OAuth client and subject are identical.

The ordinary `ark_job_*` tools reuse this boundary. Submission
resolves only an immutable allowlist of task-enabled tools, requires the target
tool's exact JWT scope before queueing, and claims the server-generated job ID
for the current tenant and principal. Capability discovery returns only
configured targets permitted by the caller. Get and cancel check ownership
before consulting the task backend, and unknown or cross-principal IDs never
return another caller's result. Arguments, prompts, URLs, and result bodies are
not added to logs or metric labels.

**Authenticated Redis task backends require snapshot encryption at startup.**
Set `FASTMCP_TASKS_ENCRYPTION_KEY` from a secret manager with at least 32 random
characters. FastMCP snapshots contain caller tokens and inbound HTTP headers;
without encryption these would be stored as plaintext. Use backend access
controls and TLS as well: snapshot encryption does not encrypt every task
argument or media result. Preserve the same key for retained tasks; replacing it
makes old snapshots unreadable and their execution fails closed.

Required-task workers atomically claim their MCP task ID in SQLite before
provider work. Claims survive restarts, so redelivery of an already-started task
fails with an ambiguous-outcome/reconciliation message instead of automatically
repeating a billable operation. This is not an exactly-once provider guarantee:
a new submission has a new ID, and deleting runtime state removes its execution
history. Keep the single-replica restriction and preserve the database whenever
retaining a Redis queue. Ownership/execution rows follow the existing state
retention policy; retained queue state must not outlive that history.
