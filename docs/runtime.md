# Runtime Services

The server lifespan owns a single `RuntimeServices` object
(`src/ark_mcp/runtime.py`) that is built once at startup and closed at
shutdown. Every tool retrieves it via `get_runtime(ctx)`. This document
describes its operational services, including concurrency limiting, the daily
budget ledger, provider-task and object-key ownership, persistence caching,
per-task persistence single-flight, and the provider retry policy.

All state lives in a single SQLite database at
`<artifact_dir>/runtime.sqlite3` (default `artifact_dir` is `.artifacts`),
shared by the ownership stores, budget ledger, and task-artifact cache. The
ownership table also stores MCP task owners and atomic execution claims. The
database is opened in WAL mode (`PRAGMA journal_mode=WAL`) with a
`busy_timeout`; each store's
synchronous `sqlite3.Connection` is guarded by a per-instance `asyncio.Lock`
and every query is dispatched to a worker thread via `asyncio.to_thread`, so
SQLite access never blocks the event loop. These stores are still
**single-process only** — horizontal scaling requires a distributed
replacement.

## `RuntimeServices` fields

| Field | Type | Built from |
|---|---|---|
| `settings` | `Settings` | input settings |
| `artifact_store` | `FilesystemArtifactStore` | `FilesystemArtifactStore(artifact_dir, ttl_seconds, downloader)` |
| `safe_downloader` | `SafeDownloader` | `SafeDownloader(timeout, connect_timeout)` |
| `ownership_store` | `SQLiteTaskOwnershipStore` | `SQLiteTaskOwnershipStore(database_path)` |
| `object_key_ownership_store` | `SQLiteObjectKeyOwnershipStore` | `SQLiteObjectKeyOwnershipStore(database_path)` |
| `budget_ledger` | `BudgetLedger` | `BudgetLedger(database_path, daily_limit_usd)` |
| `provider_limiters` | `ProviderLimiters` | `ProviderLimiters(provider_limit, principal_limit)` |
| `task_artifact_cache` | `TaskArtifactCache` | `SQLiteTaskArtifactCache(database_path, ttl_seconds, max_size)` |
| `task_artifact_locks` | `TaskArtifactPersistenceLocks` | process-local lock registry |

`close_runtime_services` closes five subsystems in order: `artifact_store`,
`ownership_store`, `object_key_ownership_store`, `budget_ledger`, and
`task_artifact_cache`. The `SafeDownloader` is closed **indirectly** —
`FilesystemArtifactStore.close()` calls `self._downloader.close()`. The
provider/principal limiters and per-task lock registry have no close method.

## Concurrency limiters (`ProviderLimiters`)

Two distinct layers, acquired together for every billable call:

- **Provider-level** — one global `asyncio.Semaphore` per provider bucket:
  `"modelark"`, `"seed-speech"`, `"vod-mediakit"`, `"tos"`, and `"s3"`.
  Each is independently sized to `provider_limit`, so the default cap is five
  concurrent calls per provider rather than five shared across all providers.
- **Principal-level** — one `asyncio.Semaphore(principal_limit)` per
  principal, lazily created and stored in a `TTLCache` keyed by
  `"<tenant_id>\0<principal_id>"` (NUL-separated). Cache get/create is
  guarded by an `asyncio.Lock`.

`acquire(provider, owner)` is an async context manager that holds **both**
the provider bucket semaphore and the principal's semaphore for the duration
of the call.

For a **local principal** (stdio transport, or HTTP local mode), the
per-principal semaphore is **skipped**: a single trusted local caller is
bounded only by the provider limit. Without this, the default
`PRINCIPAL_MAX_CONCURRENCY=3` would silently cap *all* concurrent calls across
every provider at 3 — below the per-provider limit of 5 — making parallel
local generation appear to "serialize under load." The per-principal bound
applies only to authenticated (JWT) HTTP principals, where distinct tenants
must not starve each other.

| Env var | Default | Constraint | Used as |
|---|---|---|---|
| `PROVIDER_MAX_CONCURRENCY` | `5` | `ge=1` | per-bucket provider limit |
| `PRINCIPAL_MAX_CONCURRENCY` | `3` | `ge=1` | per-principal limit (JWT HTTP principals only) |

The per-principal semaphore cache defaults to `maxsize=10_000` and
`ttl=86_400` (24h); idle principal semaphores expire after 24h. A `TTLCache`
eviction of a held semaphore dereferences it from the cache but does not
cancel its waiters.

> For parallel variation tools, a third local `asyncio.Semaphore(max_concurrent=5)`
> is created per batch by `run_variation_batch` (see [Parallel
> variations](#parallel-variations)). It composes with — and is independent
> of — these provider/principal limiters.

## Artifact persistence single-flight

`RuntimeServices.task_artifact_locks` is a process-local
`TaskArtifactPersistenceLocks` registry keyed by `(provider, task_id)`. A lock
entry exists only while a holder or waiter is active and is removed when its
user count reaches zero. Enhancement polling uses it to recheck the durable
task-artifact cache inside the critical section and share a just-created
artifact with concurrent waiters, while different task IDs remain concurrent.
Cache failures are best-effort: they emit safe warnings but do not erase a
successful provider result or an artifact that was already created.

This coordination is intentionally single-process, matching the current SQLite
state backend. A future distributed state backend requires a distributed
single-flight mechanism.

## Daily budget + cost estimation

### Budget ledger (`BudgetLedger`)

SQLite-backed, same file as the ownership store. Schema:

```sql
CREATE TABLE budget_reservations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usage_date TEXT NOT NULL,        -- UTC date (ISO)
    principal_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    product TEXT NOT NULL,
    amount_usd REAL NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('reserved', 'committed', 'released'))
)
```

Three-state lifecycle: `reserved` → `committed` (charged) **or**
`reserved` → `released` (rolled back). Only rows with `status IN
('reserved', 'committed')` count against the limit.

- `reserve(owner, estimate)` — sums the principal's spend for the current
  UTC date; if a configured limit exists and `current + amount` would exceed
  it, increments `ark_mcp_budget_rejections_total{product}` and raises
  `BudgetExceededError` **before** the provider is called. Otherwise inserts
  a `reserved` row and returns a `BudgetReservation`.
- `commit(reservation)` — marks the row `committed`.
- `release(reservation)` — marks the row `released`.

| Env var | Default | Behavior |
|---|---|---|
| `DAILY_BUDGET_USD` | `0.0` | Per-principal UTC daily limit. `0.0` (and any falsy value) normalizes to `None` → **record-only mode** (no rejection; the ledger still records everything). A positive value enables **reject mode**. |

The daily window is the UTC calendar date, scoped per `(principal_id, tenant_id)`.

### Cost estimation (`tools/_cost.py`)

The estimator used by parallel variation tools. Costs are per product and
per variation only — there is **no per-model or per-family cost table**.

| Constant | Value |
|---|---|
| `COST_PER_IMAGE` | `0.03` USD |
| `COST_PER_AUDIO_SECOND` | `0.0031` USD |
| `COST_PER_VIDEO_TASK` | `0.07` USD |
| `DEFAULT_MAX_CONCURRENT` | `5` |

`estimate_cost(product, variations, duration_seconds=0.0)`:

| `product` | Formula |
|---|---|
| `"image"` | `round(variations * 0.03, 2)` |
| `"audio"` | `round(variations * max(duration_seconds, 10) * 0.0031, 2)` — **floor of 10 seconds per variation** |
| `"video"` | `round(variations * 0.07, 2)` |
| (any other) | `0.0` |

> Note: `SeedreamFamily` (`pro`/`lite`/`4x`) and `SeedanceFamily`
> (`standard`/`fast`/`mini`) are model-binding constructs defined in
> `config/env.py`, **not** cost tiers. They do not affect the estimate.

## Provider task ownership (`SQLiteTaskOwnershipStore`)

Ownership of asynchronous provider task IDs, so one principal cannot read,
cancel, retrieve, or delete another's tasks. Schema:

```sql
CREATE TABLE IF NOT EXISTS task_ownership (
    provider TEXT NOT NULL,
    task_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(provider, task_id)
)
```

| Method | Behavior |
|---|---|
| `record(provider, task_id, owner)` | `INSERT ... ON CONFLICT(provider, task_id) DO UPDATE` — re-recording **upserts** to the new owner. |
| `require_owner(provider, task_id, owner)` | Row missing: if `owner.is_local` returns silently (local mode is permissive for unrecorded tasks), else `PermissionError`. Row present but mismatched: `PermissionError("Task is not owned by the current principal.")`. |
| `list_task_ids(provider, owner)` | returns task IDs for the provider and `(principal_id, tenant_id)`. |
| `ping()` | `SELECT 1` liveness probe (used by the `/ready` route). |
| `close()` | closes the connection. |

Every lookup is scoped by `provider` and `task_id`; owner listings also require
both `principal_id` and `tenant_id`. The `is_local` shortcut only affects the
missing-row branch — a row that exists and belongs to someone else still raises,
even in local mode.

> This is distinct from **artifact ownership**, which is stored in
> `.meta.json` sidecars next to the artifact bytes
> (`artifacts/filesystem_store.py`). See [artifacts.md](artifacts.md).

## Persistence cache

`RuntimeServices.task_artifact_cache` is a `SQLiteTaskArtifactCache` backed by
the same `runtime.sqlite3` database as the ownership store and budget ledger.
Schema:

```sql
CREATE TABLE IF NOT EXISTS task_artifacts (
    provider TEXT NOT NULL,
    task_id TEXT NOT NULL,
    artifacts_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(provider, task_id)
)
```

It caches provider task lookups → artifact references (`dict[str, ArtifactRef | None]`).
The TTL defaults to 86,400 seconds (24h), matching the longest provider media-URL
expiry window (24h for image/video; audio URLs expire in 2h), so resolved artifact
references for a provider task ID are not re-resolved while still valid. Entries
older than `PERSISTENCE_CACHE_TTL_SECONDS` are lazily ignored on read. When the
row count exceeds `PERSISTENCE_CACHE_MAX_SIZE`, the oldest entries are evicted.

Unlike the previous in-memory `TTLCache`, the SQLite-backed cache **survives
server restarts** — a restart no longer loses cached task→artifact mappings.

| Env var | Default | Used as |
|---|---|---|
| `PERSISTENCE_CACHE_MAX_SIZE` | `10_000` | max rows before oldest eviction |
| `PERSISTENCE_CACHE_TTL_SECONDS` | `86_400` | TTL in seconds; entries older than this are ignored on read |

## Retry policy (`providers/retry.py`)

`RetryPolicy` is a frozen dataclass with hard-coded defaults (no env vars):

| Field | Default |
|---|---|
| `max_attempts` | `3` |
| `base_delay_seconds` | `0.25` |
| `max_delay_seconds` | `4.0` |
| `jitter_ratio` | `0.2` |
| `retry_timeouts` | `True` |

`call_with_retry(operation, *, policy=None, sleep=asyncio.sleep,
random_value=random.random)`:

- **Only `ProviderError` is retried**, and only when `exc.retryable` **and**
  `not exc.ambiguous_completion` **and** `attempt < max_attempts` **and**
  (`policy.retry_timeouts` or `exc.code != "TIMEOUT"`). `seed_understand`
  and `seed_audio_understand` use `retry_timeouts=False`: a chat timeout is safe to retry but is not
  retried automatically, because a second full-length thinking run doubles
  latency and token cost; its 429/5xx responses are still retried.
  `ambiguous_completion=True` is deliberately non-retryable: the mutation
  may have already succeeded at the provider, so a blind retry could
  double-charge or duplicate. Reconcile via the task ID / request ID instead.
- Any non-`ProviderError` propagates immediately (no retry, no wrapping).
- **Backoff**:
  - If `exc.error.retry_after_seconds is not None` (provider `Retry-After`):
    `delay = min(retry_after_seconds, max_delay_seconds)` — **no jitter**.
  - Otherwise exponential with symmetric jitter:
    `base = min(base_delay * 2**(attempt-1), max_delay)`;
    `jitter = base * jitter_ratio * (random*2 - 1)`;
    `delay = max(0.0, base + jitter)`.
- Each retry increments `ark_mcp_retry_attempts_total{provider, operation}`.

## Billable call composition (`billed_provider_slot`)

A single async context manager ties the above together:

```python
async with billed_provider_slot(ctx, provider=..., product=..., estimated_cost_usd=...):
    # 1. reserve budget  (raises BudgetExceededError before dispatch if over)
    # 2. acquire provider + principal semaphores
    # 3. (caller runs the provider call, typically via call_with_retry)
```

Outcome handling:

| Outcome | Budget action |
|---|---|
| Success | `commit` (charged) |
| `ProviderError` with `ambiguous_completion` | `commit` (treats as billable — may have succeeded) |
| `ProviderError` with `code="TIMEOUT"` (any, including retryable read timeouts) | `commit` (a timed-out completion may still have consumed billable tokens) |
| Other `ProviderError` | `release` |
| Cancellation after the provider slot was acquired (call dispatched) | `commit` (outcome unknown) |
| Cancellation while still waiting for a limiter slot | `release` |
| Any other exception | `release` |

In all error cases the exception is re-raised. The commit/release on
cancellation is shielded, so a cancelled call never leaves a dangling
reservation.

## Error taxonomy

| Class | Module | Raised by | Meaning |
|---|---|---|---|
| `ProviderError` | `domain/errors.py` | provider gateways + transport normalizers | wraps a `NormalizedProviderError`; the single retryable error type |
| `BudgetExceededError` | `runtime.py` (extends `ValueError`) | `BudgetLedger.reserve` | daily budget would be exceeded; raised before dispatch |
| `ValueError` | builtin | `ProviderLimiters`, `RetryPolicy`, `resolve_prompts` | config/argument validation |
| `RuntimeError` | builtin | `get_runtime`, `BudgetLedger.reserve` | missing runtime services / reservation ID |
| `PermissionError` | builtin | `get_principal`, `require_owner` | missing token, missing principal/tenant claims, ownership mismatch |

`NormalizedProviderError` carries: `provider` (`modelark` or `seed-speech`),
`operation`, `http_status`, `code`, `message`, `request_id`, `retryable`,
`ambiguous_completion`, `retry_after_seconds`. Transport-layer normalizers in
`BaseHttpGateway`:

| Method | `code` | `retryable` | `ambiguous_completion` |
|---|---|---|---|
| `normalize_timeout(op)` (mutation, default `side_effect=True`) | `TIMEOUT` | `False` | `True` |
| `normalize_timeout(op, side_effect=False)` (read-only) | `TIMEOUT` | `True` | `False` |
| `normalize_connection_error` | `CONNECTION_ERROR` | `True` | `None` |
| `normalize_transport_error` | `TRANSPORT_ERROR` | `True` | `None` |

Timeout classification depends on whether the call can create provider state:

| Call | Examples | Timeout result | Auto-retried? |
|---|---|---|---|
| Mutation | Seedance/Seed 3D create, cancel, delete; Seedream/Seed Audio generate; MediaKit submit | `retryable=False`, `ambiguous_completion=True` | No — reconcile by task/request ID |
| Read-only poll | Seedance and Seed 3D `get_task`/`list_tasks`, ASR `query_asr`, MediaKit task polls | `retryable=True`, `ambiguous_completion=False` | Yes |
| Chat completion | `seed_understand`, `seed_audio_understand` | `retryable=True`, `ambiguous_completion=False` | No (`retry_timeouts=False`) |

MediaKit polls apply the same rule to their own transport normalizer:
poll timeouts and connection failures are retryable and non-ambiguous, while
submit failures stay ambiguous.

## Parallel variations (`tools/_parallel.py`)

Re-exports `DEFAULT_MAX_CONCURRENT = 5`.

- **`generate_seeds(base_seed, count)`** — `None` → provider-randomized (seed
  not recorded); `-1` → per-variation `secrets.randbelow(2**31)` (recorded);
  `N` → deterministic `(N + i) % 2**31`.
- **`resolve_prompts(base_prompt, variation_prompts, count)`** — uses
  `variation_prompts` if given; else `[base_prompt] * count`; raises if
  neither is provided.
- **`gather_with_timeout(coros, timeout)`** — per-coroutine
  `asyncio.wait_for` + `asyncio.gather(..., return_exceptions=True)`; returns
  exceptions and `asyncio.TimeoutError` **as elements**, never raises. Still
  exported but no longer used by the variation tools.
- **`variation_batch_deadline(count, settings, *, max_concurrent=5,
  provider_attempts=3, persists=True)`** — `ceil(count / max_concurrent) ×
  (request_timeout × provider_attempts + download_timeout ×
  download_attempts)`; the download term is dropped when `persists=False`
  (Seedance variations).
- **`run_variation_batch(count, factory, *, batch_deadline,
  max_concurrent=5)`** — local `asyncio.Semaphore(max_concurrent)` around
  `factory(idx, progress)`. There is no per-variation timeout; slot waiting
  never counts. At `batch_deadline`, unfinished variations are cancelled and
  classified by `VariationProgress.phase`: `queued` → `QUEUE_TIMEOUT`
  (retryable), `generating`/`persisting` → `TIMEOUT` (ambiguous), or the
  factory's recorded `partial` result (an unpersisted provider-URL artifact)
  when persisting. Unexpected exceptions map to `GATHER_ERROR`. A variation counts as succeeded iff it
  produced an `artifact` **or** a `task_id`. Returns `VariationSummary(total,
  succeeded, failed, variations)`. One variation failing never breaks the
  others; the batch always returns exactly `count` entries.

See [api-reference.md](api-reference.md) for `VariationResult` /
`VariationSummary` / `VariationError` field tables.


## MCP task execution claims

`TaskOwnershipStore.claim(provider, task_id, owner) -> bool` uses an atomic
`INSERT ... ON CONFLICT DO NOTHING`, returning true only for the first claim.
The `mcp` namespace binds protocol tasks to their application owners; the
`mcp-execution` namespace records the first entry into a required-task handler.
Neither namespace changes provider task IDs or provider ownership checks.

Execution claims are not released on failure or cancellation: provider work may
already have been accepted. A restarted worker encountering an existing claim
fails before entering the handler and asks for reconciliation. Foreground status
checks and optional background persistence remain repeatable. Claims are stored
in the same SQLite file, share its locking/transaction behavior, and follow the
existing ownership retention policy. The queue, database and configured snapshot
encryption key must stay together across restarts.
