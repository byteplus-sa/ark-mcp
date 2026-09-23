# Durable Artifacts

Provider media URLs expire — 2 hours for audio, 24 hours for image/video/3D —
so the server persists every generated output to a local store and
re-exposes it as a stable MCP resource `seed-media://artifacts/{artifact_id}`.
This document describes the artifact store, its lifecycle, and the
ownership model.

## Two expiry windows — keep them distinct

| Concept | Where | Default | Meaning |
|---|---|---|---|
| **Provider URL expiry** (`source_expires_at`) | on `ArtifactRef` | 2h (audio) / 24h (image/video) | how long the original provider URL is valid |
| **Local artifact TTL** (`expires_at`) | on `ArtifactRef`, from `ARTIFACT_TTL_SECONDS` | 604800 (7 days), must be `> 0` | how long the persisted copy is kept |

The local TTL is **independent** of the provider URL expiry. Even after the
provider URL has expired, the `seed-media://artifacts/{id}` resource remains
readable until the local TTL elapses.

## Storage backend

Two backends are available. `filesystem` stores artifacts on local disk;
`object_storage` stores them in the configured TOS/S3 bucket (via
`ObjectStorageArtifactStore`). For `object_storage`, TTL enforcement is
delegated to a bucket lifecycle policy — `delete_expired` is a no-op there.

| Env var | Default | Notes |
|---|---|---|
| `ARTIFACT_BACKEND` | `"filesystem"` | `filesystem` or `object_storage` (requires TOS/S3 credentials) |
| `ARTIFACT_DIR` | `~/.ark-mcp/artifacts` | local media + `runtime.sqlite3` state; resolved via `Path(...).expanduser().resolve()` |
| `ARTIFACT_TTL_SECONDS` | `604800` (7 days) | must be `> 0` |

`FilesystemArtifactStore` (`artifacts/filesystem_store.py`) lays out
artifacts **sharded by the first 2 characters of the UUIDv4 id**:

```text
<artifact_dir>/
└── <id[:2]>/
    ├── <id>                 # raw media bytes
    └── <id>.meta.json       # ArtifactMetadata sidecar
```

- `artifact_id` is `str(uuid.uuid4())` — a canonical UUIDv4 string. Any
  non-UUIDv4 id is rejected by `_validate_artifact_id`, and `_safe_path`
  resolves the joined path and ensures it stays inside the base directory
  (path-traversal guard).
- Writes are **atomic** (`_atomic_write`): `tempfile.mkstemp` in the shard
  dir, then `os.replace`; on any exception the temp file is unlinked.
- Every stored artifact is SHA-256 hashed (`sha256` on `ArtifactRef`).
- The store is created with `mkdir(parents=True, exist_ok=True)` in
  `__init__`; there is no explicit `ping()` method.

## `ArtifactRef` (`domain/artifacts.py`)

| Field | Type | Default | Notes |
|---|---|---|---|
| `id` | `str` | — | unique artifact id (UUIDv4) |
| `uri` | `str` | — | `seed-media://artifacts/{id}` |
| `media_type` | `MediaType` | — | `image` / `audio` / `video` |
| `mime_type` | `str` | — | e.g. `image/png` |
| `bytes` | `int \| None` | `None` | size in bytes |
| `sha256` | `str \| None` | `None` | SHA-256 hex digest |
| `created_at` | `str` | — | ISO-8601 creation timestamp |
| `expires_at` | `str \| None` | `None` | ISO-8601 local-artifact expiry |
| `source_expires_at` | `str \| None` | `None` | ISO-8601 provider URL expiry |
| `persistence_error` | `ArtifactPersistenceIssue \| None` | `None` | set when the output was generated (and billed) but not stored; see [Persistence failures](#persistence-failures) |
| `fallback_data` | `str \| None` | `None` | Base64 output bytes, only for `inline-fallback` references |
| `local_path` | `str \| None` | `None` | absolute local path written for `output_path` / `output_dir` |
| `export_error` | `str \| None` | `None` | why the requested local copy was not written |

`MediaType` is a `StrEnum`: `IMAGE`, `AUDIO`, `VIDEO`, `THREE_D`.

## The store protocol (`artifacts/store.py`)

`ArtifactStore` is a `@runtime_checkable Protocol` (all methods `async`). The
`auth` parameter is the ownership context (`PrincipalContext`, defaults to
`None`).

| Method | Signature | Returns / Raises |
|---|---|---|
| `put_base64` | `(data, media_type, mime_type, source_expires_at=None, auth=None)` | `ArtifactRef` |
| `copy_from_trusted_url` | `(url, media_type, mime_type, source_expires_at=None, auth=None)` | downloads from a trusted provider URL, stores it → `ArtifactRef` |
| `get` | `(artifact_id, auth=None)` | `StoredArtifact` |
| `locate` | `(artifact_id, auth=None)` | `ArtifactLocation` — `path` is `None` for object-storage backends |
| `delete_expired` | `(now)` | deletes expired artifacts; returns count |
| `close` | `()` | release backend resources |

> There is **no explicit `delete(artifact_id)`** on the protocol — only
> `delete_expired(now)`.

### `put_base64` / `copy_from_trusted_url` flow (`_store_bytes`)

1. Enforce `get_media_limits()` per media category against `len(raw)`
   (image/audio 10 MiB, video 200 MiB).
2. Validate the MIME via `validate_image_mime` / `validate_audio_mime` /
   `validate_video_mime`.
3. Resolve `owner = auth or AuthContext()`.
4. Generate `artifact_id = str(uuid.uuid4())`, compute `sha256`.
5. `expires_at = now + ttl_seconds`; build `ArtifactRef(uri=f"seed-media://artifacts/{artifact_id}", ...)`.
6. Atomic-write the artifact bytes, then atomic-write the
   `ArtifactMetadata` sidecar.
7. Increment `ark_mcp_artifact_operations_total{operation="put", status="success", media_type}`.

`copy_from_trusted_url` passes a host-suffix allowlist
(`.bytepluses.com`, `.byteplus.com`, `.byteplusvod.com`, `.bytedance.com`,
`.bytednsdoc.com`, `.volces.com`, `.tos-ap-southeast.bytepluses.com`) as the
`trusted_hosts` predicate to `SafeDownloader.download`. If the downloaded
`content_type` differs from the supplied `mime_type`, it logs
`artifact_mime_mismatch` and overrides the MIME with the actual content type.

### Download and upload retries

A GET on a provider output URL has no side effects, so `SafeDownloader`
retries retryable failures — timeouts, network errors, and 408/429/5xx
responses — up to `ARTIFACT_DOWNLOAD_MAX_ATTEMPTS` times (default 3), waiting
1s, 2s, 4s, ... between attempts. Each attempt is bounded by
`ARTIFACT_DOWNLOAD_TIMEOUT_SECONDS` (default 120). Both backends use the same
settings. Policy failures — untrusted host, rejected redirect, oversized body,
expired source — are never retried.

The object-storage backend also retries transient failures of its data and
metadata uploads (object keys are fixed per artifact, so a repeated PUT is
idempotent); a final failure surfaces as `storage_failed`.

## Persistence failures

A billed output is never dropped because storage failed. When persistence
fails after the retries above, the tool call still succeeds and the returned
`ArtifactRef` carries `persistence_error` (`ArtifactPersistenceIssue`):

| Field | Meaning |
|---|---|
| `code` | `untrusted_output_host`, `output_too_large`, `invalid_output_mime`, `source_expired`, `download_failed`, or `storage_failed` |
| `message` | credential- and URL-safe explanation |
| `retryable` | whether persisting again may succeed |
| `artifact_limit_bytes` | size limit of the durable artifact policy for this media type |
| `source_url_expires_at` | when the unpersisted provider URL expires |

The reference takes one of two shapes:

| `id` | Used for | Where the bytes are |
|---|---|---|
| `provider-url` | URL outputs (Seedream `url`, Seedance video/last frame, Seed 3D files) and inline outputs that also came with a provider URL | `uri` is the temporary provider URL, valid until `source_expires_at` |
| `inline-fallback` | Inline Base64 outputs with no provider URL (Seed Audio, Seedream `b64_json`) | `fallback_data`, up to `ARTIFACT_INLINE_FALLBACK_MAX_BYTES` (default 8 MiB; `0` disables) |

Inline Base64 is retried once against the store before falling back. An inline
output larger than `ARTIFACT_INLINE_FALLBACK_MAX_BYTES` with no provider URL
still fails the call. Invalid or oversized Base64 is a validation error, not a
persistence failure.

To recover a `provider-url` reference, call `seed_media_persist_url` with its
`uri`, `media_type`, `mime_type`, and `source_expires_at` before the URL
expires. The tool downloads only trusted provider hosts, through the same
downloader and limits, and returns a new durable `ArtifactRef`.

In variation batches, a variation whose output was generated but not stored
reports `error.phase="persisting"`.

For Seedance and Seed 3D get tools, an unpersisted result is not cached, so the
next poll with `persist_output=true` tries again while the provider URL is
still valid. MediaKit get tools keep their per-output persistence status
(`persisted`, `failed`, ...) alongside the provider `source_url`.

## Local copies

On stdio transport, `output_path` / `output_dir` on generation and get tools,
and `destination_path` on `seed_media_export_artifact`, write a copy of the
durable artifact to a local file inside an allowed output root (client MCP
roots, else `OUTPUT_ROOTS`). Bytes come from the local store file, or from the
store for object storage; `inline-fallback` references are written from
`fallback_data`. A `provider-url` reference cannot be written locally and gets
an `export_error` asking you to persist it first. A failed local write never
affects the durable artifact or fails the call. See
[Security](security.md#local-output-paths) for the path policy.

## Artifact ownership (`.meta.json`)

`ArtifactMetadata` (versioned, `schema_version: Literal[2] = 2`) is written
beside each artifact:

| Field | Type |
|---|---|
| `schema_version` | `2` |
| `ref` | `ArtifactRef` |
| `principal_id` | `str` |
| `tenant_id` | `str` |

`get(artifact_id, auth)` enforces ownership: `owner = auth or AuthContext()`,
and raises `PermissionError("Artifact is not owned by the current
principal.")` unless `metadata.principal_id == owner.principal_id` **and**
`metadata.tenant_id == owner.tenant_id`. A v1 `ArtifactRef`-only sidecar
(without ownership) is migrated to v2 with `principal_id="local"`,
`tenant_id="local"`.

> This is distinct from **Seedance task ownership**, which lives in the
> SQLite `task_ownership` table in `runtime.py`. Artifact ownership lives in
> JSON sidecars, not in SQLite.

## The resource URI

The `seed-media://artifacts/{id}` URI is constructed inline inside
`FilesystemArtifactStore._store_bytes` and stored on `ArtifactRef.uri`. The
MCP resource handler (`get_artifact`) in `server.py` reads it with
`auth=component_auth(resolved_settings, "artifacts:read")` and passes the
resolved principal to `runtime.artifact_store.get(...)`.

> `artifacts/registry.py` is a **compatibility guard**, not the active
> registry. `get_artifact_store()` always raises
> `RuntimeError("The global artifact registry was removed; obtain the store
> from RuntimeServices in the FastMCP lifespan context.")`. Obtain the store
> from `RuntimeServices` (via the FastMCP lifespan context), never via this
> legacy function.

## Expiry and cleanup

`delete_expired(now)` iterates `*.meta.json` under the base directory and
deletes the artifact + sidecar when `metadata.ref.expires_at <= now`.
Failures are logged as `artifact_cleanup_error` and skipped; it logs
`artifacts_expired_deleted count=N` when something was deleted, and returns
the deleted count.

A background sweeper runs inside the server lifespan every
`ARTIFACT_SWEEP_INTERVAL_SECONDS` (default 3600). It calls `delete_expired`
plus the ownership/budget/cache `prune`/`prune_expired` methods, each with its
own error isolation. For `ARTIFACT_BACKEND=object_storage`, `delete_expired`
is a no-op — enforce TTL there with a bucket lifecycle policy instead.

## What is not persisted here

- **Seedance task ownership** and the **budget ledger** live in SQLite
  (`runtime.sqlite3`), not in the artifact store. See
  [runtime.md](runtime.md).
- **Provider task lookups** are cached in `RuntimeServices.task_artifact_cache`
  (`SQLiteTaskArtifactCache`, same database as ownership/budget) to avoid
  re-resolving still-valid provider URLs. The cache survives server restarts.
