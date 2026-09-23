---
title: Agent Workflow Ergonomics — understanding output, strict JSON, timeout retryability, batch helpers, queue visibility
type: plan
status: in-review
created: 2026-09-21
updated: 2026-09-22
reviewed: 2026-09-22 (subagent plan review; findings folded in)
tags: [seed_understand, seedream, seedance, media_upload, retry, artifacts, ergonomics]
source:
  - Field report from a multi-element production run (beat/motion reviews, 7-variant Seedream batch, 15 uploads, 5-task Seedance polling rounds), 2026-09
  - BytePlus ModelArk Chat Completions API reference (response_format, thinking) — URL to be recorded during Phase 2 verification
  - BytePlus ModelArk Seedance task list API (filter.task_ids) — URL to be recorded during Phase 3 verification
related:
  - plans/PLAN_CONCURRENCY_AND_TIMEOUT_REMEDIATION.md
  - plans/PLAN_PARALLEL_GENERATION.md
  - plans/PLAN_SEEDANCE_REFERENCE_ERGONOMICS.md
  - docs/artifacts.md
  - docs/tools.md
---

# Agent Workflow Ergonomics

## Summary

A long agent-driven production session found five friction points. Code
inspection shows that several of them are **bugs rather than missing features**:

| # | Report | Root cause found in code | Kind |
|---|--------|--------------------------|------|
| 1 | `seed_understand` results were 50–80 KB, mostly `reasoning_content` | `seed_understand` copies `message.reasoning_content` into every result ([seed_understand.py:227](../src/ark_mcp/tools/seed_understand.py)), and the model thinks even when `thinking=false`, because the field is then omitted and the model default applies. Callers only want the final answer. No output-size controls exist. | Bug + feature |
| 2 | Malformed JSON from a motion review | No `response_format` passthrough. `ChatCompletionProviderRequest` has no such field. | Feature |
| 3a | Understanding `TIMEOUT` returned `retryable=false` | `BaseGateway.normalize_timeout` marks every timeout as ambiguous and non-retryable, including read-only chat completions | Bug |
| 3b | 7 Seedream variants failed with `TIMEOUT` | `run_variation_batch` wraps `_guarded()` in `asyncio.wait_for`, so time spent **waiting on the semaphore** (`DEFAULT_MAX_CONCURRENT=5`) counts against the per-variation timeout. The same timeout also covers provider generation and the artifact download. Variants 6–7 start with a partly used budget. | Bug |
| 3c | "Provider output download timed out" | `SafeDownloader` marks it `retryable=True`, but nothing retries it. In `seedream_generate_image` the `ArtifactPersistenceError` is uncaught, so a **billed** image is lost even though its provider URL is valid for 24h. Variations report it as `UNEXPECTED_ERROR`. | Bug |
| 4a | 15 separate `media_upload` calls | No batch variant | Feature |
| 4b | 5 separate `seedance_get_task` calls per round | `seedance_list_tasks(task_ids=[...])` exists. The provider list response is parsed as full `SeedanceTaskResponse` objects (`schemas.py:196-201`), but `to_task_summary` drops video, error, and persistence, so it can't replace `get_task` | Feature gap |
| 4c | Copy-from-`.artifacts/` step after every generation | `seed_media_export_artifact(destination_path=...)` exists, but it is a separate call per artifact | Feature |
| 5 | Tasks `queued` 20+ min with no position/ETA | ModelArk does not expose queue position. The server surfaces nothing derived: no time in queue, no expiry deadline, no service tier, no count of the caller's own concurrent tasks. | Feature (bounded) |

Fix order: bugs that lose paid output or mislabel errors first (Phase 1),
then output-size and JSON controls (Phase 2), then batch and output-path
ergonomics (Phase 3), then queue visibility (Phase 4).

## Non-goals

- Streaming (`stream: true`) to MCP clients. It is considered only as an
  internal transport option in Phase 1.4.
- Server-side JSON repair. Strict schema enforcement at generation time
  replaces it, and callers keep a `content` fallback.
- A real queue position or ETA from ModelArk. The provider does not publish
  one, and we will not invent one.
- Auto-retrying provider **mutations** (task creation, image generation) after
  a timeout. Those stay ambiguous; see Phase 1.2.

---

## Phase 1 — Correctness: retryability and lost outputs

### 1.1 Operation-aware timeout normalization

`src/ark_mcp/providers/base.py` `normalize_timeout(operation)` always returns
`retryable=False, ambiguous_completion=True`.

Change the signature:

```python
@classmethod
def normalize_timeout(cls, operation: str, *, side_effect: bool = True) -> ProviderError:
    ...
    retryable = not side_effect
    ambiguous_completion = side_effect
    message = (
        f"Request timed out during '{operation}'. The operation may have succeeded. "
        "Do not retry blindly; reconcile using the task ID or request ID."
        if side_effect else
        f"Request timed out during '{operation}'. No server-side state was created; "
        "it is safe to retry (the retry is billed again)."
    )
```

Pass `side_effect=False` for:
- `SeedUnderstandingService.generate` (`chat_completion`)
- Seedance `get_task` / `list_tasks`, Seed3D get/list, and ASR result queries
- VOD get-task queries. These do **not** go through `normalize_timeout`: they
  use `normalize_ambiguous_transport_error` (`vod_mediakit/enhancement.py:146`,
  plus `transcode.py`, `subtitles.py`, `separate_voice.py`). Give that helper
  the same `side_effect` keyword and pass `False` from the GET/query paths.

Keep `side_effect=True` (the default) for all create, generate, cancel, and
delete calls.

Auto-retry policy for these calls:
- **Read-only GET/list calls**: auto-retry through `call_with_retry` (they are
  cheap).
- **`chat_completion`**: mark it `retryable=True` and `ambiguous_completion=False`,
  but do **not** auto-retry inside the call. A second 10-minute attempt would
  double latency and token cost. `RetryPolicy` has no predicate today
  (`retry.py:17-30`), and `max_attempts=1` would also turn off 429/5xx retries.
  Add `retry_timeouts: bool = True` to `RetryPolicy`: when it is false,
  `call_with_retry` re-raises `code == "TIMEOUT"` errors right away but still
  retries other retryable errors. Use `RetryPolicy(retry_timeouts=False)` at
  the chat call site.
- `billed_provider_slot` then **releases** the reservation for a non-ambiguous
  timeout. That is incorrect for chat: tokens may have been consumed. Add an
  explicit `billed_on_timeout` rule: commit the reservation when
  `exc.code == "TIMEOUT"`, whatever `ambiguous_completion` says.
- `billed_provider_slot` catches only `Exception` (`runtime.py:942`), so a
  cancelled variation or job (`asyncio.CancelledError`) leaves its reservation
  in the `reserved` state, where it still counts against the budget. Add an
  explicit `except BaseException` branch. It commits if the provider request
  was already sent (the result is ambiguous) and releases otherwise, then
  re-raises.

### 1.2 Variation timeout must not count queue wait

`src/ark_mcp/tools/_parallel.py` `run_variation_batch` wraps each variation,
*including its wait for a slot*, in `asyncio.wait_for(timeout)`
(`_parallel.py:65, 91-96`). The timeout is `request_timeout_ms` (600s,
`seedream_generate_image_variations.py:163`), the same value as the httpx
timeout. It has to cover:

1. waiting on the local batch semaphore (`DEFAULT_MAX_CONCURRENT=5`)
2. waiting on the shared `modelark` provider limiter inside
   `billed_provider_slot` (5 slots shared by *every* concurrent tool, plus a
   per-principal limit of 3 over HTTP; see `runtime.py:79-80, 123-128`)
3. up to 3 `call_with_retry` attempts, each allowed 600s by httpx
4. the artifact download (plus the retries added in 1.3)

Moving `wait_for` inside the local semaphore alone would still count item 2
and would still be smaller than items 3 and 4 combined.

**Fix: drop the per-variation `wait_for`.** Every phase is already bounded:
httpx bounds each provider attempt, `call_with_retry` bounds the number of
attempts, and 1.3 bounds each download attempt and the number of attempts. The
wrapper adds only false timeouts.

Keep one safety net: an overall `batch_deadline` in `run_variation_batch`.
Compute it as `ceil(count / max_concurrent) × per_variation_budget`, where
`per_variation_budget = request_timeout × max_attempts + download_timeout × download_attempts`.
Use `asyncio.timeout` around the `gather`. Anything still running at the
deadline is cancelled, and the cancellation path in 1.1 handles its budget.

Phase reporting: the factory takes a small mutable holder,
`factory(idx, progress: VariationProgress)`, where `VariationProgress.phase`
is `"queued" | "generating" | "persisting"`. The factory updates it before
each step, and `run_variation_batch` reads it when the deadline cancels a
variation. It can't read a closure variable from outside, so the holder is
required. Map the phases like this:
- `queued` → `code="QUEUE_TIMEOUT"`, `retryable=True`, `ambiguous_completion=False`
- `generating` → `code="TIMEOUT"`, `retryable=False`, `ambiguous_completion=True`
- `persisting` → the provider URL fallback from 1.3

`VariationError` gains an optional `phase` field (with a description). Update
every `run_variation_batch` caller: the Seedream, Seed Audio, Seedance 2.0,
and Seedance 2.5 variation tools.

### 1.3 Retry artifact downloads internally; never drop a billed output

1. **Internal download retry.** Add a `retry_download()` helper in
   `security/safe_downloader.py`. It retries when `SafeDownloadError.retryable`
   is set: 3 attempts with backoff of 1s, 2s, 4s. It never retries
   `source_expired`, `too_large`, or URL-policy errors. Use it from **both**
   `FilesystemArtifactStore.copy_from_trusted_url` and
   `ObjectStorageArtifactStore.copy_from_trusted_url`. A GET on a provider URL
   has no side effects. Add the `ARTIFACT_DOWNLOAD_MAX_ATTEMPTS` setting
   (default 3).
2. **Separate download timeout.** The two backends set it differently. The
   filesystem backend uses the `SafeDownloader` default of `timeout=120.0`. The
   object-storage backend passes `request_timeout_ms` (600s,
   `object_storage_store.py:86-89`). Add `ARTIFACT_DOWNLOAD_TIMEOUT_SECONDS`
   (default 120), wire it into both, and document that it applies to each
   attempt.
3. **Retry object-storage writes.** The TOS/S3 `put` in
   `ObjectStorageArtifactStore._store_bytes` is idempotent under a fixed
   object key. Wrap it in `call_with_retry` so a transient 5xx doesn't discard
   bytes that were already downloaded.
4. **Degrade instead of failing, one item at a time.** Today
   `seedream_generate_image` and `seedream_edit_image` persist items in a loop
   (`seedream_generate_image.py:203-221`) and don't catch
   `ArtifactPersistenceError`. If item 2 fails, item 1's already stored
   artifact disappears from the output too. Change this so each item is
   handled separately:
   - **URL outputs** (Seedream `url`, Seedance video, Seed3D, VOD): on final
     failure, return an unpersisted `ArtifactRef` (`id="provider-url"`,
     `uri=<provider URL>`, `source_expires_at`) with a `persistence_error` on
     that item. Stored items are always kept.
   - **Inline base64 outputs** (Seedream `b64_json`, and **all** Seed Audio
     output, which goes through `put_base64` only: `seed_audio_generate.py:258-260`,
     `seed_audio_generate_variations.py:174`): there is no URL to fall back
     to, and a store failure here is a local disk or bucket error. Retry the
     store once. If it still fails, return the base64 inline in a
     `fallback_data` field, capped at `ARTIFACT_INLINE_FALLBACK_MAX_BYTES`
     (default 8 MB). Above the cap, return the error. Never drop it silently.
5. **Shared persistence-issue model.** Promote `VodArtifactPersistenceIssue`
   (`_vod_shared.py:10-25`) to `domain/artifacts.py` as
   `ArtifactPersistenceIssue`. It must be a **strict superset** of the VOD
   model: keep `code`, `message`, `retryable`, and `artifact_limit_bytes`, with
   the same names and descriptions. Add only an optional
   `source_url_expires_at`. The provider URL goes on the fallback
   `ArtifactRef.uri`, **never** in the issue's `message`, which stays safe to
   log and free of URLs as documented. Keep `VodArtifactPersistenceIssue` as a
   type alias so the VOD output schemas don't change.
6. **Fix the variation fallthrough.** In every variation tool, catch
   `ArtifactPersistenceError` explicitly instead of letting it reach
   `except Exception` and be reported as `UNEXPECTED_ERROR`.
7. **Recover later with `seed_media_persist_url`.** New tool, input
   `{url, media_type, mime_type}`, output `ArtifactRef`. It calls
   `artifact_store.copy_from_trusted_url`, so it has the same trusted-host
   allowlist, redirect re-validation, and size limits. Arbitrary URLs are
   rejected, which prevents server-side request forgery. Scope `media:upload`,
   registered `("optional", "media:upload")`. Annotations:
   `readOnlyHint=False`, `idempotentHint=False`, `openWorldHint=True`.
   Document it in `docs/tools.md` and `docs/artifacts.md`.

**Behavior change (call it out):** Seedream persistence failures currently
reach the client as tool errors. After this phase they come back as
successful results with `persistence_error` set. Clients that depend on
`is_error` need to check the new field.

### 1.4 Understanding transport resilience (required: thinking is always on, see 2.1)

A non-streaming completion with deep thinking sends no bytes until it
finishes, so a long reasoning run can hit the httpx read timeout
(`BYTEPLUS_REQUEST_TIMEOUT_MS`, 10 min). Two options, in order of preference:
- Add `SEED_UNDERSTANDING_TIMEOUT_MS` (default: inherit), so understanding can
  have a longer budget than image generation. It already runs as a required
  background job, so a longer wait doesn't block a foreground request.
- Later: stream internally (`stream: true`), accumulate the deltas, and return
  the same non-streaming output. Bytes keep arriving, so the read timeout
  becomes a true idle timeout. This is deferred because the SSE parser is new
  surface area.

### Phase 1 tests
- `tests/unit/test_retry.py`: `retry_timeouts=False` re-raises TIMEOUT right
  away but still retries 429 and 5xx.
- Provider tests: the `side_effect` flag produces the expected
  `retryable`/`ambiguous` values in `normalize_timeout` and in
  `normalize_ambiguous_transport_error`. GET timeouts are auto-retried; chat
  timeouts are not.
- `tests/unit/test_runtime.py`: timeouts and cancellations commit or release
  the reservation as specified. A cancelled slot never stays `reserved`.
- `tests/unit/test_parallel_helpers.py`: with `max_concurrent=1`, `count=3`,
  and a factory that sleeps 0.6× `request_timeout`, all 3 succeed (this fails
  today). Also with the shared provider limiter held by another task. When the
  batch deadline hits, the phase is reported from `VariationProgress`.
- `tests/unit/test_safe_downloader.py`: `retry_download` retries a
  `ReadTimeout` and succeeds. It does not retry `source_expired`, `too_large`,
  or `untrusted_host`.
- `tests/unit/test_filesystem_store.py` and the object-storage store tests:
  both backends retry the download, and the object store retries a 5xx on put.
- Seedream single/edit/variations: one item's persistence failure keeps the
  other items' stored artifacts, and the failed item gets a provider URL and
  `persistence_error`. The budget is committed.
- Seed Audio: a store failure returns `fallback_data` below the cap and the
  error above it.
- VOD output schema snapshot unchanged (the alias works).

## Phase 2 — `seed_understand` output control and strict JSON

### 2.1 Always think; never return reasoning

Decision (2026-09-22): `seed_understand` always runs with deep thinking, and
the reasoning trace never reaches the tool output. Callers get only the final
answer.

Request side (`SeedUnderstandingService.build_request`):
- Always send `thinking={"type": "enabled"}`. Do not rely on the model
  default.
- Keep `reasoning_effort`. It is now the only lever on how long the model
  thinks. Decision (2026-09-22): when unset, it is pinned to `"medium"`, not
  left to the provider default, so latency and token cost stay predictable.
  The field becomes `Literal["low", "medium", "high"] = "medium"`, and the
  value is always sent. Callers can still choose `low` or `high`. Its
  description states the default and that it controls thinking depth, not
  whether thinking happens.
- The `thinking` input field becomes a deprecated no-op for one release: it is
  still accepted, so existing callers and strict clients keep validating, but
  it is ignored, and `thinking=false` logs a `thinking_flag_ignored` warning.
  Its description says so. Remove it in the next minor release.
- If a configured binding has `supports_thinking=False`, send neither
  `thinking` nor `reasoning_effort` for it (so the `"medium"` default is not
  sent either) instead of failing. The capability registry stays the source of
  truth.

Response side:
- Remove `reasoning_content` from `UnderstandingChoice` (in the shared domain
  model). Drop `message.reasoning_content` right after parsing the provider
  response, so it is never logged, persisted, written by `save_to`, or
  returned.
- Keep one piece of metadata: `UnderstandingUsage.reasoning_tokens: int | None`,
  from `usage.completion_tokens_details.reasoning_tokens` when the provider
  reports it (add that field to `ChatUsage`). This explains token cost without
  leaking the trace.
- Breaking output change: call it out in release notes.
- Existing tests that assert the old behavior must be rewritten, not deleted:
  `tests/integration/test_seed_understand_tool.py:160-192` (reasoning is returned) and
  `:269-300` (effort dropped when thinking is off).

Consequence for timeouts: every call now does the longer thinking run, so
Phase 1.4 (`SEED_UNDERSTANDING_TIMEOUT_MS`, and possibly internal streaming)
moves from optional to required, and ships with or before this change.

### 2.2 Output-size controls

New `SeedUnderstandInput` fields:

```python
save_to: str | None = Field(
    None,
    description=(
        "Optional absolute file path (stdio transport only). The parsed answer is written "
        "atomically to this path: JSON (pretty-printed) when response_format produced valid "
        "JSON, otherwise UTF-8 text. Only the final answer is written, never reasoning. "
        "Must resolve under an allowed output root (see OUTPUT_ROOTS)."
    ),
)
return_content: Literal["full", "summary", "none"] = Field(
    "full",
    description=(
        "How much of the answer to inline in the tool result. 'summary' returns the first "
        "2,000 characters plus content_chars; 'none' returns only metadata (use with save_to)."
    ),
)
```

Output changes (all fields described; `UnderstandingChoice` is a shared model):
- `UnderstandingChoice.reasoning_content` is removed (see 2.1).
- New `UnderstandingChoice.content_chars: int` and `content_truncated: bool`.
- New `UnderstandingChoice.parsed: dict | list | None`: set when a JSON
  `response_format` was requested and the content parsed.
- New `UnderstandingChoice.schema_violation: SchemaViolation | None`, where
  `SchemaViolation{path, message, finish_reason}` is a new model in
  `domain/models.py` with every field described.
- New `SeedUnderstandOutput.saved_path: str | None` and `saved_bytes: int | None`.
- `save_to` writes files, so `seed_understand`'s `TOOL_ANNOTATIONS` change
  from `readOnlyHint: True` to `False`. `save_to` is validated before the
  provider call, so a bad path is rejected before billing.

Also persist the full final answer (never the reasoning) as a durable text
artifact (`MediaType.TEXT`, new; or `application/json` under the existing
store) when `save_to` is not set and the output exceeds
`UNDERSTANDING_INLINE_MAX_CHARS` (default 20,000). Return its `artifact_id` so
nothing is lost when inline content is truncated. This needs a small
artifact-store extension (a text MIME allowlist); if that is too large for this
phase, ship `save_to` first and track the artifact separately.

### 2.3 Strict JSON: `response_format`

Add to `ChatCompletionProviderRequest`:

```python
response_format: dict[str, Any] | None = None
```

New input:

```python
class UnderstandingJsonSchema(BaseModel):
    name: str = Field(..., pattern=r"^[A-Za-z0-9_-]{1,64}$", description="Schema name sent to the provider.")
    schema_: dict[str, Any] = Field(..., alias="schema", description="JSON Schema (draft 2020-12 subset) the answer must satisfy.")
    strict: bool = Field(True, description="Ask the provider to enforce the schema during decoding.")

class UnderstandingResponseFormat(BaseModel):
    type: Literal["text", "json_object", "json_schema"] = Field(..., description=...)
    json_schema: UnderstandingJsonSchema | None = Field(None, description="Required when type='json_schema'.")
```

Behavior:
1. Capability gate. Add `supports_response_format: frozenset[str]` to
   `UnderstandingCapabilities`. Reject unsupported types with a clear
   `ValueError` before billing. Confirmed (2026-09-22): every Seed 2.1 binding
   enforces `json_schema` while thinking is enabled, so every configured
   binding gets `{"text", "json_object", "json_schema"}`. There is a single
   path: the schema is always sent to the provider and enforced there. No
   local-enforcement fallback and no `schema_enforcement` output field. The
   capability flag stays so a future binding without support is rejected
   before billing, not silently downgraded.
2. Validate the supplied schema locally with `jsonschema` (`Draft202012Validator.check_schema`).
   Cap its size at 64 KB. `jsonschema` is not a direct dependency today
   (`pyproject.toml:11-22`), so add it with `uv add jsonschema`. Build the
   provider payload with `model_dump(by_alias=True, exclude_none=True)`, so
   `schema_` is sent as `schema`.
3. After the response returns, `json.loads(content)`, then validate against the
   schema as a defensive check. This should not fail when the provider
   enforces the schema, but it can when output is cut off
   (`finish_reason="length"`). On success, set `parsed`. On failure, return a
   success result with `parsed=None` and a `schema_violation: {path, message}`
   field, so the caller can decide. Never fail the call and lose the billed
   text. Log `schema_violation` as a warning metric so provider-enforcement
   regressions show up.
4. Optional `json_retry: int = 0` (max 2): on a parse or validation failure
   where `finish_reason != "length"`, re-ask with the validator error
   appended. Do **not** retry when the output was cut off by `max_tokens`
   (`finish_reason="length"`): the retry would be cut off at the same limit.
   Return `schema_violation` with a hint to raise `max_tokens`. Each retry
   gets its own `billed_provider_slot` and cost estimate, and the description
   says retries are billed.

This lets template-factory schemas be passed verbatim as
`response_format.json_schema.schema`.

### Phase 2 tests
- `build_request` always sends `thinking.type="enabled"` (including when the
  deprecated `thinking=false` is passed), sends `reasoning_effort="medium"` when
  unset, and passes `response_format` through.
- A provider response containing `reasoning_content` produces output with no
  reasoning anywhere: not in the result, `save_to` file, artifact, or logs.
  `reasoning_tokens` is reported when the provider includes it.
- `save_to` writes atomically, is rejected outside `OUTPUT_ROOTS`, and is
  rejected over HTTP transport.
- A schema violation produces `schema_violation`, not an error.
- `json_retry` is skipped when `finish_reason="length"`, and each retry
  reserves its own budget.
- The `schema` alias is serialized as `schema` in the provider payload.
- `tests/integration/test_mcp_conformance.py`: every new input and output field
  has a description, and the `seed_understand` annotations reflect `save_to`.

---

## Phase 3 — Batch helpers and `output_path`

### 3.0 Shared: output root policy

A new `src/ark_mcp/security/output_paths.py` holds one writer that `save_to`,
`output_path`, and `seed_media_export_artifact` all use:

```python
def resolve_output_path(raw: str, *, roots: Sequence[Path], kind: Literal["file", "dir"]) -> Path
def write_output_file(dest: Path, data_or_src: bytes | Path, *, roots: Sequence[Path],
                      overwrite: bool, expected_sha256: str | None) -> WriteOutcome
```

Rules:
- **stdio only.** Raise the same error text `seed_media_export_artifact` uses
  today.
- **Absolute paths only.** Relative paths are rejected. The export tool
  accepts them today.
- **Roots (proposed; see open question 1).** Use the client's MCP roots
  (`roots/list`) when the client provides them, then `OUTPUT_ROOTS`
  (comma-separated). If neither exists, path-writing features are
  **disabled** and return a clear error. The server's CWD is **not** used as a
  default: stdio clients often launch servers with CWD `/` or `$HOME`, which
  would make the restriction meaningless.
- **Containment:** `expanduser().resolve()`, then require the result to be
  under one of the resolved roots.
- **TOCTOU-safe write.**
  1. Create missing parents one level at a time, re-checking after each
     `mkdir` that the parent still resolves inside the root.
  2. Open the final parent with `os.open(..., O_DIRECTORY | O_NOFOLLOW)`.
  3. Write the temp file through `dir_fd`, `fsync` it, then either
     `os.link(tmp, name, src_dir_fd=…, dst_dir_fd=…)` when `overwrite=False`
     (this fails if the target exists, which makes it exclusive) or
     `os.replace` with `dir_fd` when `overwrite=True`.
  4. Unlink the temp file.
- **Idempotent re-export.** When the destination exists and its SHA-256 equals
  `expected_sha256`, return `WriteOutcome.already_present`, a success, not an
  error. Repeat `seedance_get_task` polls return cached artifacts and would
  otherwise hit a "destination exists" error on every poll after the first.
  This also keeps `seed_media_export_artifact`'s `idempotentHint: True`
  truthful.
- **`overwrite: bool = False`** is a new described input field on every tool
  that takes `output_path`/`output_dir`/`save_to`/`destination_path`.
- **Validate before billing.** Every generation tool resolves and validates
  the path *before* the provider call, so a bad path never produces paid
  output that can only fail with an `export_error`.
- **Retrofit `seed_media_export_artifact.destination_path`** to use this writer.
  **Behavior changes for the changelog:** it currently accepts any path,
  including relative paths, and silently overwrites existing files. After this
  change it requires an absolute path inside a root and refuses to overwrite a
  different file unless `overwrite=true`.

### 3.1 `output_path` on generation tools

Add optional `output_path: str | None` to `seedream_generate_image`,
`seedream_edit_image`, `seed_audio_generate`, `seedance_get_task` (when
`persist_output`), the Hyper3D/Hitem3D get-task tools, and the VOD get-task
tools. Add `output_dir` to the `*_variations` tools.

- `output_path` is a file path, or a directory when it ends in `/`. A directory
  gets `<artifact_id>.<ext>`. For a result with more than one artifact
  (`max_images>1`, or a Seedance video plus last frame), `output_path` **must**
  be a directory. A file path is rejected before billing.
- Behavior: the artifact is persisted to the store as it is today (the durable
  resource URI stays), then written with `write_output_file` (3.0). The source
  depends on the backend. The filesystem backend copies from `location.path`.
  The object-storage backend has no local path (`location.path is None`), so
  the bytes come from `artifact_store.get()`, exactly as
  `seed_media_export_artifact` does today. Move that branching into
  `artifacts/export.py` so both callers share it.
- For a provider-URL fallback artifact (1.3), nothing is written locally.
  `local_path` is None, and `export_error` explains why.
- `ArtifactRef` gains `local_path: str | None` (with a description). This is a
  shared domain model change.
- A copy failure never fails the tool. It sets `ArtifactRef.local_path=None`
  plus an `export_error`.
- This works through `ark_job_submit`, because the path is part of the tool
  arguments.

### 3.2 `media_upload_batch`

New tool, modeled on `media_presign_batch` (per-item results; one failure does
not fail the batch).

```python
class MediaUploadBatchItemInput(BaseModel):   # same fields as MediaUploadInput minus expires_in_seconds
    media_type, mime_type, data | file_path, key_prefix
class MediaUploadBatchInput(BaseModel):
    items: list[MediaUploadBatchItemInput] = Field(..., min_length=1, max_length=50)
    expires_in_seconds: int | None
    max_concurrent: int = Field(4, ge=1, le=8)
class MediaUploadBatchItem(BaseModel):
    index: int; file_path: str | None; url: str | None; object_key: str | None
    expires_at: str | None; bytes: int | None; error: str | None
class MediaUploadBatchOutput(BaseModel):
    items: list[MediaUploadBatchItem]; succeeded: int; failed: int
```

- Extract the per-item body of `media_upload` into `_upload_one()` and reuse
  it, so there is no duplicated validation.
- The aggregate byte cap per call is `MEDIA_UPLOAD_BATCH_MAX_BYTES` (default
  500 MB), checked before any upload. For `file_path` items, use `stat()`. For
  inline `data` items, use the decoded size (`check_base64_size`). The MCP
  request itself also bounds inline items, but they still count toward the cap.
- `file_path` MIME inference: allow `mime_type` to be omitted in batch items,
  inferring it from the extension through `mimetypes`, still validated by
  `media_policy`.
- Register it exactly like `media_upload`: `BackgroundToolSpec("required", "media:upload")`
  (`background_jobs.py:43`, `server.py:237`), with the same task-mode
  registration in `server.py`. No new scope.

### 3.3 `seedance_get_tasks`

New read-only tool:

```python
class SeedanceGetTasksInput(BaseModel):
    task_ids: list[str] = Field(..., min_length=1, max_length=50)
    persist_output: bool = False
    output_dir: str | None = None
class SeedanceGetTasksOutput(BaseModel):
    tasks: list[SeedanceTaskOutput]            # full shape, same as seedance_get_task
    errors: list[SeedanceTaskLookupError]      # {task_id, code, message}
    counts: dict[SeedanceTaskStatus, int]      # quick triage
    all_terminal: bool                         # true when nothing is queued/running
```

- **One provider call.** `SeedanceTaskListResponse.data` is already typed
  `list[SeedanceTaskResponse]`, the full task object including `content` and
  `error` (`schemas.py:149-158, 196-201`). Only the server's
  `to_task_summary` discards those fields. So fetch with one
  `list_tasks(filter.task_ids=…, page_size=len(ids))` call and map each item
  through the same converter `seedance_get_task` uses. Fall back to `get_task`
  for each ID only if a live check (open question 2) shows the list payload
  leaves `content` out.
- **Dedupe** `task_ids` before fetching (keep the first occurrence's order).
- **Serialize persistence per task.** `seedance_get_task` has no single-flight
  lock around persistence today, while the VOD tools use
  `task_artifact_locks.acquire` (`vod_get_enhancement_task.py:154`). Add the
  same lock around the check-cache → download → store → cache sequence in the
  shared Seedance persistence helper, so overlapping polls or batches never
  download the same video twice. `seedance_get_task` benefits too.
- Keep the existing `persistence_requires_task` gate that `seedance_get_task`
  applies to persistence.
- Ownership checks match `seedance_get_task` for each ID. An unowned ID goes to
  `errors`; it does not fail the batch.
- Also add `hyper3d_get_tasks` / `hitem3d_get_tasks` only if they are
  requested. They are not in scope here.

---

## Phase 4 — Queue visibility (derived, honest)

ModelArk returns no queue position or ETA. Add derived fields to
`SeedanceTaskOutput` and `SeedanceTaskSummary` (all described, and all marked
as server-derived estimates):

| Field | Source |
|-------|--------|
| `queued_seconds: int \| None` | `now - created_at` while `status=="queued"`; `updated_at - created_at` once it has left the queue (approximate) |
| `running_seconds: int \| None` | `now - updated_at` while `running` |
| *(no new field)* | `service_tier` is already returned in `SeedanceTaskOutput.settings` (`models.py:150`). `queue_hint` just references it. `flex` tasks are expected to queue longer. |
| `expires_at: str \| None` | `created_at + execution_expires_after`. When `execution_expires_after` is None, use the documented provider default (verify it; 48h for `default` tier) and set `expires_at_estimated: bool = True`. |
| `queue_hint: str \| None` | human-readable, for example "Queued 21m on flex tier; provider expires the task at 14:05Z. ModelArk does not publish queue position." |

Optionally, keep a per-process rolling median of observed
`queued → running` latency per `(model, service_tier)` in the runtime state,
fed by status transitions seen in `get_task`. Expose it as
`typical_queue_seconds` with a `sample_size`. Emit it only when
`sample_size >= 5`. Mark it clearly as local observation, not a provider SLA.
Also export it as a Prometheus histogram (`seedance_queue_seconds`) in
`observability/metrics.py`.

---

## Documentation and skills (same unit of work, per AGENTS.md)

- `docs/tools.md`, `docs/api-reference.md`: new tools (`media_upload_batch`,
  `seedance_get_tasks`), new fields, and the `retryable` semantics table
  (TIMEOUT by operation).
- `docs/security.md`: output-root policy, MCP roots, symlink and TOCTOU handling, and the `seed_media_persist_url` host allowlist.
- `docs/multi-generation.md`: variation timeout and phase semantics, and the batch deadline.
- `docs/runtime.md`: budget commit/release on timeout and cancellation.
- `docs/configuration.md`: `OUTPUT_ROOTS`, `ARTIFACT_INLINE_FALLBACK_MAX_BYTES`, `ARTIFACT_DOWNLOAD_MAX_ATTEMPTS`,
  `ARTIFACT_DOWNLOAD_TIMEOUT_SECONDS`, `SEED_UNDERSTANDING_TIMEOUT_MS`,
  `UNDERSTANDING_INLINE_MAX_CHARS`, and `MEDIA_UPLOAD_BATCH_MAX_BYTES`.
- `docs/artifacts.md`: `output_path`/`local_path`, provider-URL degradation,
  and the `persistence_error` fields.
- `docs/troubleshooting.md`: "Result too large", "TIMEOUT retryable semantics",
  and "Why no queue position".
- `.agents/skills/ark-mcp/`: update the tool inventory. Make these the
  recommended defaults: `save_to` for reviews (note that reasoning is never returned),
  `response_format` for template schemas, batch tools, and `output_path`.
- `README.md` tool count and list.
- `background_jobs.py`: register `media_upload_batch` and `seedance_get_tasks`.

## Rollout order and sizing

| Phase | Scope | Est. |
|-------|-------|------|
| 1 | Timeout semantics, variation timer, download retry, provider-URL fallback | M |
| 2 | Always-on thinking, reasoning stripped from output, size controls, `save_to`, `response_format` | M (live smoke test still asserts `json_schema` and thinking on each binding) |
| 3 | Output-root policy, `output_path`, `media_upload_batch`, `seedance_get_tasks` | L |
| 4 | Derived queue fields, optional rolling median | S |

Each phase ships as its own PR. Behavior changes that need changelog entries:
- **Phase 1:** Seedream and Seed Audio persistence failures become successes
  with `persistence_error`/`fallback_data`, instead of tool errors. Timeout
  errors on read-only calls become `retryable=true`.
- **Phase 2:** `reasoning_content` is removed, `thinking` is a no-op,
  `reasoning_effort` defaults to `medium`, and `seed_understand` is no longer
  `readOnlyHint`.
- **Phase 3:** `seed_media_export_artifact.destination_path` must be absolute
  and inside a root, and no longer overwrites silently.

## Implementation status (2026-09-22)

All four phases are implemented on branch `feat/agent-workflow-ergonomics`.
Where the code departs from the plan:

- **Understanding inline size (2.2):** `UNDERSTANDING_INLINE_MAX_CHARS` and
  the durable text artifact were dropped. `return_content="full"` (the
  default) never truncates, `"summary"` keeps 2,000 characters, and `save_to`
  keeps the full answer. Removing reasoning addressed most of the size
  problem.
- **`seedance_get_task` with a file `output_path` (3.1):** instead of
  rejecting the path, the video is written to it and the last frame beside
  it with a `-last-frame` suffix. At call time it is unknown whether a last
  frame exists, so rejection would be arbitrary.
- **MCP roots in background jobs (3.0):** when no client session is
  reachable (`ark_job_submit` workers), `roots/list` is unavailable and the
  writer falls back to `OUTPUT_ROOTS`.
- **Deferred:**
  - internal SSE streaming for understanding (1.4); only
    `SEED_UNDERSTANDING_TIMEOUT_MS` shipped
  - the rolling queue-latency median and its Prometheus histogram (Phase 4)
  - `hyper3d_get_tasks` / `hitem3d_get_tasks`
- **Assumption to verify live:** when a task omits
  `execution_expires_after`, the 48h default is assumed and flagged as
  `expires_at_estimated=true`.
- **Portable writer:** platforms without `dir_fd` support (Windows) use a
  portable atomic writer with realpath containment checks, without the
  `O_NOFOLLOW` guarantees.

## Resolved decisions

- 2026-09-22: `seed_understand` always thinks; reasoning is never returned (2.1).
- 2026-09-22: every Seed 2.1 binding enforces `json_schema` while thinking.
  Provider-side enforcement is the only path (2.3).
- 2026-09-22: `reasoning_effort` defaults to `medium` when unset (2.1).
- 2026-09-22: output roots come from the client's MCP roots, then `OUTPUT_ROOTS`;
  otherwise path writing is disabled. The CWD default was rejected.

## Open questions

1. Seedance list payload: the code already parses list items as full
   `SeedanceTaskResponse` objects, so the plan assumes one list call. Confirm
   with a live call that `content.video_url` and `error` are populated for
   succeeded and failed tasks.
2. `execution_expires_after` default when the provider leaves it out. Confirm
   the value for each service tier.
