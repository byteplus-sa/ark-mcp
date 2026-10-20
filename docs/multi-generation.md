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
per-variation control like distinct prompts or seeds), the `variations`
field on the generation tools makes multiple independent API calls in
parallel. There are no separate `*_variations` tools; every variation shares
the call's other inputs, including reference media.

### Products with variation support

| Tool | Product | Uses native batch? | Parallel mechanism |
|---|---|---|---|
| `seedream_generate_image` with `variations` 2-10 | Seedream (any model; cannot be combined with `max_images > 1`) | No (always parallel calls) | `run_variation_batch` |
| `seed_audio_generate` with `variations` 2-5 | Seed Audio | No | `run_variation_batch` |
| `seedance_create_task` with `variations` 2-5 | Seedance (every configured model) | No | `run_variation_batch` |

### How it works

The shared helper [`run_variation_batch`] in `tools/_parallel.py`:

1. For Seedream, derives one seed per variation from the request's `seed`
   (the base seed) via `generate_seeds`:
   - `seed` omitted → provider randomizes each (seed not recorded).
   - `seed=-1` → client picks random seeds (recorded for reproducibility).
   - `seed=N` → deterministic sequence `[N, N+1, N+2, ...]` modulo
     `2147483648`.

   Seed Audio and Seedance take no seed; their variations differ by sampling
   or by `variation_prompts`.
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
`ARTIFACT_DOWNLOAD_MAX_ATTEMPTS`. Seedance variations only submit tasks,
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

On stdio transport, `seedream_generate_image` and `seed_audio_generate`
accept `output_path` (plus `overwrite`); with `variations > 1` it must be a
directory ending in `/`, and every persisted variation is written there as
`<artifact_id>.<ext>`. Each variation's `ArtifactRef` reports `local_path` or
`export_error`. See [Tools](tools.md#writing-outputs-to-local-paths).

### Seedance variations

Seedance variations are fundamentally different from Seedream/Seed Audio
because each variation creates a **separate provider task**. They are not a
separate tool: call `seedance_create_task` with `variations` (1-5) and
optionally `variation_prompts` (exactly one per variation). The background
result lists every variation in `tasks` (`index`, `task_id` or `error`) with
`submitted` and `failed` counts. Poll all task IDs at once with
`gen_task_get` (1-50 task IDs, one provider list call per page of 20;
`all_terminal` tells you when to stop, and `output_dir` writes finished videos
locally). **Never resubmit `seedance_create_task` to poll**: that starts new
billed tasks. Use `dry_run=true` first to check the request bodies and
estimated cost of every variation without billing. `draft=true` works with
variations; `draft_task_id` requires `variations=1`.

### Cost

All variation tools log a cost estimate before dispatching calls. The
estimate is based on the number of variations and the product type. Actual
billing is determined by the provider and reflected in the per-response
usage fields.

## Choosing the right mechanism

| Scenario | Use |
|---|---|
| Multiple images, same prompt, Lite or 4.x model | `max_images` on `seedream_generate_image` |
| Multiple images, Pro model | `variations` on `seedream_generate_image` |
| Per-variation prompts or seeds | `variations` plus `variation_prompts` (and `seed` on Seedream) |
| Multiple audio outputs | `variations` on `seed_audio_generate` |
| Check requests and cost first | `dry_run=true` on `seedream_generate_image` or `seedance_create_task` |
| Multiple video tasks | `seedance_create_task` with `variations` (1-5), then `gen_task_get` |

[`run_variation_batch`]: ../src/ark_mcp/tools/_parallel.py
