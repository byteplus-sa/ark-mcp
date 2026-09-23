# Multi-Generation

The server supports two mechanisms for producing multiple outputs from one
background job: **native provider batch** (one API call, many outputs) and
**client-side parallel variation** (many independent API calls with bounded
concurrency). By default a client submits through `ark_job_submit`, receives
an Ark job ID, and polls `ark_job_get`; a task-capable client may instead
receive an MCP task ID and poll `tasks/get`. Either terminal response contains
the same typed output, referred to below as the **background result**.

## Native provider batch

Seedream supports generating multiple images in a single provider API call
via the `max_images` parameter. This is a native capability of the BytePlus
API and is only supported by **Lite** and **4.x** model families.

**How it works:**

1. The client passes `max_images` (1-15) on `seedream_generate_image`.
2. The handler validates it against the capability registry:
   - Lite and 4.x allow batch; Pro rejects `max_images > 1`.
3. The provider adapter translates `max_images` into
   `sequential_image_generation: "auto"` with
   `sequential_image_generation_options: {"max_images": N}`.
4. The provider returns all N images in a single response. Each image is
   persisted as a separate `ArtifactRef` and returned in the background result.

This is a **single `POST /images/generations` call** — one request, one
response, multiple outputs. It is the most efficient path and should be
preferred over variations when the model supports it.

| Model family | Supports `max_images` | Max images |
|---|---|---|
| **Pro** (`seedream_pro`) | No | 1 |
| **Lite** (`seedream_lite`) | Yes | 15 |
| **4.x** (`seedream_4x`) | Yes | 15 |

## Client-side parallel variations

When a product does not support native batch (or when the client needs
per-variation control like distinct prompts, seeds, or media inputs), the
`*_variations` tools make multiple independent API calls in parallel.

### Products with variation support

| Tool | Product | Uses native batch? | Parallel mechanism |
|---|---|---|---|
| `seedream_generate_image_variations` | Seedream | No (always parallel calls) | `run_variation_batch` |
| `seed_audio_generate_variations` | Seed Audio | No | `run_variation_batch` |
| `seedance_create_task_variations` | Seedance | No | `run_variation_batch` |
| `seedance_2_5_create_task_variations` | Seedance 2.5 | No | `run_variation_batch` |

### How it works

The shared helper [`run_variation_batch`] in `tools/_parallel.py`:

1. Generates distinct seeds for each variation via `generate_seeds`:
   - `base_seed=None` → provider randomizes each (seed not recorded).
   - `base_seed=-1` → client picks random seeds (recorded for reproducibility).
   - `base_seed=N` → deterministic sequence `[N, N+1, N+2, ...]` modulo
     `2147483648`.
2. Resolves prompts via `resolve_prompts`: either per-variation prompts or
   the same base prompt repeated N times.
3. Launches N tasks, each calling the provider API independently.
4. Bounds concurrency with an `asyncio.Semaphore` (default from
   `DEFAULT_MAX_CONCURRENT` in `tools/_cost.py`).
5. Waits for all of them under one batch deadline (see below), then builds a
   `VariationSummary` with per-variation success/failure tracking.

### Deadlines and failure phases

Individual variations have **no wall-clock timeout** of their own. Every phase
is already bounded where it happens (httpx timeouts, the retry policy, and the
artifact downloader's per-attempt timeout), and time spent waiting for a
concurrency slot never counts against a variation. A variation queued behind
others therefore cannot time out just because it waited.

One batch deadline is a safety net for a stuck batch:

```text
waves          = ceil(variations / max_concurrent)
per_variation  = request_timeout × 3
               + download_timeout × download_attempts   (persisting tools only)
batch_deadline = waves × per_variation
```

`request_timeout` is `BYTEPLUS_REQUEST_TIMEOUT_MS`; `download_timeout` and
`download_attempts` are `ARTIFACT_DOWNLOAD_TIMEOUT_SECONDS` and
`ARTIFACT_DOWNLOAD_MAX_ATTEMPTS`. Seedance variation tools only submit tasks,
so their deadline omits the download term.

A variation that hits the batch deadline, or whose output could not be
stored, records where it stopped in `error.phase` (ordinary provider errors
leave `phase` null and keep their provider `code`, `retryable`, and
`ambiguous_completion`):

| `phase` | Meaning | Typical code | Retry? |
|---|---|---|---|
| `queued` | Never started before the batch deadline | `QUEUE_TIMEOUT` (`retryable=true`) | Safe to retry |
| `generating` | Provider call in flight at the deadline | `TIMEOUT` (`ambiguous_completion=true`) | Do not retry blindly; the provider may have completed it |
| `persisting` | Output generated, storing it failed | upper-cased persistence code (e.g. `STORAGE_FAILED`) | Output was billed; see below |

Most storage failures do not produce an error at all: a variation whose
output has a provider URL comes back as a success with an unpersisted
`ArtifactRef` (`id="provider-url"`, `persistence_error` set), and inline
Base64 comes back as `id="inline-fallback"` with `fallback_data`. The
`persisting` error appears only when neither fallback is possible (for
example inline output above `ARTIFACT_INLINE_FALLBACK_MAX_BYTES`). If the
deadline hits while a variation is persisting and its output URL is already
known, the variation returns that unpersisted output instead of an error. Recover it with `seed_media_persist_url`. A persistence failure never
drops a billed output; see [Artifacts](artifacts.md#persistence-failures).

### Writing variations to a local directory

On stdio transport, `seedream_generate_image_variations` and
`seed_audio_generate_variations` accept `output_dir` (plus `overwrite`) to
write every persisted variation to a local directory as
`<artifact_id>.<ext>`. Each variation's `ArtifactRef` reports `local_path` or
`export_error`. See [Tools](tools.md#writing-outputs-to-local-paths).

### Seedance variations

Seedance variations are fundamentally different from Seedream/Seed Audio
because each variation creates a **separate provider task**. The client first
retrieves the variation summary from the background result, then polls the
provider task IDs to retrieve results. Poll them all at once with
`seedance_get_tasks` (1-50 task IDs, one provider list call, `all_terminal`
tells you when to stop, and `output_dir` writes finished videos locally), or
one at a time with `seedance_get_task`. The actual video generation runs
asynchronously on the provider side.

### Cost

All variation tools log a cost estimate before dispatching calls. The
estimate is based on the number of variations and the product type. Actual
billing is determined by the provider and reflected in the per-response
usage fields.

## Choosing the right mechanism

| Scenario | Use |
|---|---|
| Multiple images, same prompt, Lite or 4.x model | `max_images` on `seedream_generate_image` |
| Multiple images, Pro model | `seedream_generate_image_variations` |
| Per-variation prompts, seeds, or reference images | `*_variations` tool |
| Multiple audio outputs | `seed_audio_generate_variations` |
| Multiple video tasks | `seedance_create_task_variations`, then `seedance_get_tasks` |

[`run_variation_batch`]: ../src/ark_mcp/tools/_parallel.py
