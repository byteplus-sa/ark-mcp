# Durable Artifacts

Provider media URLs expire — 2 hours for audio, 24 hours for image/video —
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

`MediaType` is a `StrEnum`: `IMAGE`, `AUDIO`, `VIDEO`.

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
(`.bytepluses.com`, `.byteplus.com`, `.bytedance.com`, `.bytednsdoc.com`,
`.volces.com`, `.tos-ap-southeast.bytepluses.com`) as the `trusted_hosts`
predicate to `SafeDownloader.download`. If the downloaded `content_type`
differs from the supplied `mime_type`, it logs `artifact_mime_mismatch` and
overrides the MIME with the actual content type.

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
