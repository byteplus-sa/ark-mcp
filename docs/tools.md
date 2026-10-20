# Tools Reference

The server exposes a conditional set of typed tools. `seed_media_get_artifact`,
`seed_media_export_artifact`, and `seed_media_persist_url` are always
available, provider tools are registered only when their credentials
are configured, and `media_upload` and `media_presign` are registered when
object storage credentials (TOS or S3) are present. Each
tool accepts a Pydantic input model and returns a Pydantic output model as
structured content. All tools accept a `ctx: Context` parameter for progress
reporting and logging.

## Tool Contract for MCP Clients

Every tool is self-describing through its JSON schema — MCP clients do not
need external documentation to understand inputs and outputs. The following
contract is enforced for all tools:

- **Tool descriptions.** Each tool's `description` comes from the handler
  function's docstring. It explains what the tool does, what it accepts, and
  what it returns.
- **Field descriptions on every input and output field.** Every Pydantic
  `Field` includes a `description` that explains the field's meaning, units,
  valid values, and constraints. This includes nested and shared domain models
  (`ArtifactRef`, `VariationSummary`, `SeedanceTaskSettings`, etc.).
- **Tool annotations.** Each tool declares MCP hints (`readOnlyHint`,
  `destructiveHint`, `idempotentHint`, `openWorldHint`) so clients can reason
  about side effects before calling.
- **Output schemas.** Each tool registers a `output_schema` (via
  `model_json_schema()`) so clients get typed structured content, not just
  text.
- **Error handling.** Provider errors are returned as `ToolResult` with
  `is_error=True` and a human-readable text summary. The declared output
  schema always represents the success shape; error results carry no
  `structured_content` to avoid schema-validation conflicts under strict MCP
  clients.

### Long-running tool execution

Long-running tools run through the ordinary `ark_job_*` tools described below
by default, because most clients cannot negotiate MCP task augmentation; a
client that can negotiate it may call the tools with native task metadata
instead. In this reference, **background result** means the original tool
result under terminal `ark_job_get.result`, or the terminal `tasks/get`
response on the native path.

The following tools require background execution. Their direct contracts
advertise `execution.taskSupport="required"` with a two-second recommended poll
interval:

- `media_upload` (1-50 files per call)
- `seed_audio_generate` (single clip or variations)
- `speech_to_text`
- `seedream_generate_image` (generation, editing, variations, and dry runs)
- `seedance_create_task` (every Seedance model, including variations and dry runs)
- `hyper3d_create_task`
- `hitem3d_create_task`
- `omnihuman_create_task` (when `BYTEPLUS_OMNIHUMAN_ENABLED=true`)
- `seed_understand`
- `seed_audio_understand`
- `vod_enhance_video`
- `vod_transcode_video`
- `vod_separate_audio`
- `vod_add_subtitles`
- `vod_remove_subtitles`

A client submits these through `ark_job_submit`, receives an Ark job ID without
holding a tool call open, and polls `ark_job_get` until it reaches a terminal
status. A task-capable client can instead call the tool with task metadata and
poll `tasks/get`. Either way the terminal response contains the final tool
output, and a foreground direct call to a required target is rejected before
any provider request is made. For Seedance, Seed 3D, and MediaKit
create/submit tools, the background job covers the provider submission; its
result contains the provider task ID. **Poll Seedance and 3D tasks with
`gen_task_get` and MediaKit tasks with `vod_get_task`; never resubmit a create
tool to poll**, because that starts a second billed task.

The following retrieval tools advertise `execution.taskSupport="optional"`:

- `gen_task_get`
- `seed_media_persist_url`
- `omnihuman_get_task`
- `vod_get_task`

Use foreground execution for quick status checks with persistence disabled.
Use background execution when `persist_output=true` and a completed media file
may need to be downloaded and copied into durable storage. Artifact reads,
presigning, list operations, and cancel/delete operations remain foreground.

### Writing outputs to local paths

On stdio transport, generation and retrieval tools can write their output
straight to a local file, so a client does not need a separate
`seed_media_export_artifact` call:

| Tool | Field | Notes |
|---|---|---|
| `seedream_generate_image` | `output_path` | Must be a directory (ending with `/`) when `variations > 1` or `max_images > 1`; extra images written to a file path get `-2`, `-3` suffixes |
| `seed_audio_generate` | `output_path` | Must be a directory (ending with `/`) when `variations > 1` |
| `vod_get_task` | `output_path` | Must be a directory (ending with `/`) for `operation="separate_audio"`, which writes every track |
| `gen_task_get` | `output_dir` | One file per artifact: every output of each succeeded task |
| `seed_understand`, `seed_audio_understand` | `save_to` | Writes the final answer, not media |

`output_path` names a file, or a directory when it ends with `/`; files written
into a directory are named `<artifact_id>.<ext>`. Every tool that has one of
these fields also has `overwrite` (default `false`). The rules are the same
everywhere:

- **stdio only.** The fields are rejected on HTTP transport.
- **Absolute paths inside an allowed output root.** The roots are the client's
  MCP roots (`roots/list`) when the client provides them, otherwise
  `OUTPUT_ROOTS` (comma-separated absolute directories). With neither, path
  writing is disabled and the field is rejected. A background job
  (`ark_job_submit`) has no MCP roots, so it always needs `OUTPUT_ROOTS`;
  `persist_output=true` itself works without it.
- **Validated before the provider call**, so a bad path never produces paid
  output that cannot be written.
- **Needs a persisted artifact.** `output_path`/`output_dir` require
  `persist=true` (generation tools) or `persist_output=true` (retrieval tools).
- **Exclusive, symlink-safe writes.** An existing file is replaced only with
  `overwrite=true`; a file that already holds identical bytes counts as
  success, so repeated polls stay idempotent.
- **A failed local write never fails the call.** The durable artifact is kept
  and the reference reports `local_path` (on success) or `export_error`.

See [Security](security.md#local-output-paths) for the full output-root
policy.

## ark_list_models

**Call this first.** Lists the models this server is configured to call, with
the limits the server validates before submitting, and reports which optional
services are enabled. Read-only, no provider calls, always registered, and
needs no scope.

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `kind` | `"video"` \| `"image"` \| `"3d"` \| `"understanding"` | No | Filter to one kind; omit for all |

### Output

- **`models`**: one entry per configured binding with `model_id` (the exact
  value to pass as `model`), `family`, `kind`, `is_default`, `tool`, and a
  kind-specific limits object:
  - `video`: resolutions, `duration_seconds` (inclusive; `-1` auto is always
    accepted), ratios, reference-count maxima, Draft support and final
    resolution, audio-only input, `task_types`, `output_formats`.
  - `image`: reference maximum, output formats, batch support, sizes,
    transparent-background support.
  - `3d`: text-to-3D support, reference maximum, file formats, seed support.
  - `understanding`: modalities accepted by the named `tool`, media-part maximum, thinking,
    reasoning efforts, response formats. Both the default model for requests
    with audio and the default for requests without audio are marked
    `is_default`.
- **`services`**: `modelark`, `seedance_2_5_premium`, `seed3d`, `seed_audio`,
  `speech_to_text`, `vod_mediakit`, `omnihuman`, `object_storage`,
  `asset_library` and `asset_delete`, each with `enabled` and, when disabled,
  a `reason` naming the setting to change (never a secret value).

Models are listed only when their service is enabled. The Seedance 2.5
Premium binding exists only when its whitelist flag is on.

The audio-model entry names `seed_audio_understand`, advertises only `audio`
with an eight-clip limit, and uses the server-configured model without a
`model` argument. This remains true when audio and visual defaults share an
ID. Image/video requests use `seed_understand`; discovery does not imply
that the two handlers can accept mixed audio/visual inputs.

## Background-job tools

`ark_job_capabilities`, `ark_job_submit`, `ark_job_get`, and `ark_job_cancel`
are ordinary MCP tools and the default way to run long operations, since
task-augmented tool calls are not yet widely supported by clients.
They do not make long operations synchronous: `ark_job_submit` validates the
selected target, durably enqueues it on the same Docket worker used by native
MCP tasks, and returns an Ark job ID. All four tools are always registered;
capabilities include only provider tools enabled by configuration and permitted
by the caller's JWT scopes.

### ark_job_capabilities

Takes no input. Returns a list of `targets`; each target contains `tool_name`,
`task_mode`, `required_scope`, `description`, and the original tool's
`input_schema`. This operation is read-only and idempotent.

### ark_job_submit

| Field | Type | Required | Description |
|---|---|---|---|
| `tool_name` | string | Yes | A target returned by `ark_job_capabilities` |
| `arguments` | object | Yes | Exact arguments object accepted by the original tool, normally `{"input": {...}}` |

Returns `job_id`, `target_tool`, `status="working"`, `created_at`, `ttl_ms`, and
`poll_after_ms`. Submission is non-idempotent. If the response is lost, do not
blindly resubmit because the worker or provider may already have accepted the
operation.

### ark_job_get

| Field | Type | Required | Description |
|---|---|---|---|
| `job_id` | string | Yes | Ark job ID returned by `ark_job_submit` |

Returns job status and timestamps. A terminal response includes the original
MCP tool result under `result`, preserving its `content`, `structured_content`,
`is_error`, and `meta` fields. The Ark job ID is distinct from a provider task
ID contained inside the original result.

### ark_job_cancel

| Field | Type | Required | Description |
|---|---|---|---|
| `job_id` | string | Yes | Ark job ID owned by the current principal |

Returns `status="cancelled"` after cooperative local cancellation. It does not
guarantee that provider-side work already accepted upstream was cancelled.
Unknown and cross-principal IDs use the same unavailable response boundary. In
local auth mode jobs are process-local: a job created by a different `ark-mcp`
process is also unavailable, so submit and poll through the same server (or run
one shared HTTP server for all local clients).

## seed_media_get_artifact

Retrieve persisted media inline by artifact ID.

**Annotations:** `readOnlyHint=True`, `destructiveHint=False`,
`idempotentHint=True`, `openWorldHint=False`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `artifact_id` | string | Yes | Artifact ID returned by a previous generation call |

### Output

Returns `SeedMediaGetArtifactOutput` with `artifact_id`, `media_type`,
`mime_type`, `sha256`, `bytes`, and Base64 `data`.

## seed_media_export_artifact

Locate or copy a persisted artifact on the local filesystem. Returns the
absolute on-disk path instead of streaming Base64 through the MCP context.
Stdio transport only — the client and server must share a filesystem.

**Annotations:** `readOnlyHint=False`, `destructiveHint=False`,
`idempotentHint=True`, `openWorldHint=False`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `artifact_id` | string | Yes | Artifact ID returned by a previous generation call |
| `destination_path` | string | No | Absolute file path inside an allowed output root where the server writes an atomic copy; omit to return the canonical store path |
| `overwrite` | boolean | No | Replace an existing destination file with different content (default: false) |

`destination_path` follows the [local output path](#writing-outputs-to-local-paths)
rules: it must be absolute and inside an allowed output root (client MCP roots,
or `OUTPUT_ROOTS`), and an existing file with different content is replaced
only when `overwrite=true`. An existing file with identical content is always
accepted. With the object-storage artifact backend, `destination_path` is
required because the store keeps no local file.

### Output

Returns `SeedMediaExportArtifactOutput` with `artifact_id`, `path`,
`media_type`, `mime_type`, `bytes`, `sha256`, and `copied`. `copied` is `false`
when `path` is the canonical store location (filesystem backend) and `true`
when the artifact was copied to `destination_path` (or an identical copy was
already there).

## seed_media_persist_url

Persist a temporary provider output URL as a durable artifact. Use it when a
generation or retrieval result returned an artifact with `id="provider-url"`
and a `persistence_error`: the output was generated and billed, but could not
be stored at the time. Call it before the provider URL expires (2 hours for
audio, 24 hours for image, video, and 3D). Always registered; uses the
`media:upload` JWT scope.

Only trusted BytePlus provider hosts are accepted. The URL goes through the
same SSRF-resistant downloader, host allowlist, redirect re-validation, and
size limits as generation-time persistence; any other URL is rejected.

**Execution:** Optional background job. Large videos are safer in the
background.

**Annotations:** `readOnlyHint=False`, `destructiveHint=False`,
`idempotentHint=False`, `openWorldHint=True`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `url` | string | Yes | Temporary HTTPS provider URL, typically the `uri` of an `ArtifactRef` whose `id` is `provider-url` |
| `media_type` | "image" \| "audio" \| "video" \| "three_d" | Yes | Logical media type of the output |
| `mime_type` | string | Yes | Expected MIME type (e.g. `video/mp4`); a specific provider `Content-Type` takes precedence |
| `source_expires_at` | string | No | ISO-8601 expiry of the provider URL, copied from the original `ArtifactRef.source_expires_at` |

### Output

Returns `SeedMediaPersistUrlOutput` with `artifact`, the new durable
`ArtifactRef`. A persistence failure is a tool error that names the failure
code and whether it is retryable.

## vod_enhance_video

Enhance a public HTTPS source video through BytePlus VOD AI MediaKit. This
tool is registered only when `BYTEPLUS_VOD_MEDIAKIT_API_KEY` is set and uses
the `vod:enhance` JWT scope.

**Annotations:** `readOnlyHint=False`, `destructiveHint=False`,
`idempotentHint=False`, `openWorldHint=True`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `video_url` | URL | Yes | Public HTTPS source; private and link-local destinations are rejected |
| `tool_version` | `"fast"` \| `"standard"` \| `"professional"` | No | Enhancement tier (default `"professional"`). `fast` is speed-first lightweight upscaling on its own provider endpoint (input up to 2K); `standard` balances speed and quality; `professional` is the highest-quality, slowest, and costliest tier |
| `resolution` | `"240p"` \| `"360p"` \| `"480p"` \| `"540p"` \| `"720p"` \| `"1080p"` \| `"2k"` \| `"4k"` \| `"6k"` \| `"8k"` | No | Target resolution level; `fast` accepts up to `4k`. Mutually exclusive with `resolution_limit`; defaults to `4k` when neither is set |
| `resolution_limit` | integer | No | Target short-side pixels, 128–4320 (128–2160 for `fast`), scaled proportionally. Mutually exclusive with `resolution` |
| `bitrate_level` | `"low"` \| `"medium"` \| `"high"` | No | Target bitrate tier (default `"high"`); ignored when `bitrate` is set |
| `bitrate` | integer | No | Exact target bitrate in kbps, 10–150000; takes precedence over `bitrate_level` |
| `fps` | number \| null | No | Target frame rate, 15–120 (default 24); `null` keeps the source frame rate |
| `scene` | `"common"` \| `"ugc"` \| `"short_series"` \| `"aigc"` \| `"old_film"` | No | Scenario preset (default `"common"`); only takes effect for `standard` and is not sent for `fast` |
| `enhance_style` | `"hd"` \| `"natural"` | No | Sharper `hd` or softer `natural` look for `standard`/`professional`; rejected for `fast` |
| `project` | string | No | Defaults to `default`; serialized upstream as `Project` |
| `input_duration_seconds` | number | No | Reserved for future pricing support; currently produces no estimate |
| `persist` | boolean | No | Best-effort artifact copy (default: true) |

### Output and execution limits

The verified provider contract returns `status="accepted"` with a `task_id` and
`operation="enhance"` for [`vod_get_task`](#vod_get_task). The non-idempotent
POST is not retried automatically
because a timeout can have ambiguous completion. A completed output always
preserves `source_url`. Persistence is reported as `not_applicable`, `persisted`,
`failed`, or `not_requested`, and a failed artifact copy does not erase provider success.
Durable video copies remain subject to the 200 MiB limit.

The success-body mapping remains provisional and rejects unknown response
shapes. `estimated_cost_usd` is always null until convenience-endpoint pricing
is confirmed.

## vod_transcode_video

Submit an asynchronous BytePlus VOD AI MediaKit video transcoding task. This
tool is registered only when `BYTEPLUS_VOD_MEDIAKIT_API_KEY` is set and uses the
`vod:transcode` JWT scope.

**Annotations:** `readOnlyHint=False`, `destructiveHint=False`,
`idempotentHint=False`, `openWorldHint=True`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `video_url` | URL | Yes | Public HTTPS source; private and link-local destinations are rejected |
| `container_format` | `"MP4"` \| `"FLV"` \| `"MPEGTS"` | No | Output container format (default: `MP4`) |
| `video` | VodTranscodeVideoOptions | No | Transcoding options (defaults reproduce the verified portrait-to-720x720 letterbox profile) |

**VodTranscodeVideoOptions:**

| Field | Type | Default | Description |
|---|---|---|---|
| `codec` | `"h264"` \| `"h265"` | `"h264"` | Output video codec |
| `scale_type` | `0` \| `1` \| `2` | `2` | `0` follow source, `1` long/short-side limit, `2` width/height limit |
| `scale_mode` | `0` \| `1` \| `2` | `2` | `0` no upsampling, `1` stretch, `2` letterbox with black bars |
| `scale_width` | integer \| null | `null` | Target width px [0,4320]; only when `scale_type=2` (defaults to 720) |
| `scale_height` | integer \| null | `null` | Target height px [0,4320]; only when `scale_type=2` (defaults to 720) |
| `scale_short` | integer \| null | `null` | Target short side px [0,4320]; only when `scale_type=1` |
| `scale_long` | integer \| null | `null` | Target long side px [0,4320]; only when `scale_type=1` |
| `bitrate_mode` | `"crf"` \| `"abr"` \| `"cbr"` | `"crf"` | Bitrate control mode |
| `bitrate_crf` | integer | `25` | CRF quality [0,51]; only used when `bitrate_mode=crf` |
| `bitrate_kbps` | integer | `2000` | Bitrate in kbps [10,50000] |
| `fps_mode` | `"vfr"` \| `"cfr"` | `"vfr"` | Frame-rate mode; only takes effect after `fps` is set |
| `fps` | integer \| null | `null` | Target frame rate [1,240]; unset keeps source rate |
| `is_hdr_to_sdr` | boolean | `true` | Convert HDR to SDR; false keeps HDR |

### Output

Returns `VodTranscodeVideoOutput` with `status="accepted"`, the `task_id` and
`operation="transcode"` to poll via [`vod_get_task`](#vod_get_task), and a
heuristic `recommended_poll_after_ms`.
The non-idempotent POST is not retried automatically because a timeout can have
ambiguous completion.

### Example

```json
{
  "video_url": "https://media.example.com/portrait.mp4",
  "container_format": "MP4",
  "video": {
    "scale_type": 2,
    "scale_width": 720,
    "scale_height": 720,
    "scale_mode": 2
  }
}
```

## vod_add_subtitles

Burn subtitles into a public HTTPS video with MediaKit. Supply either
`subtitle_url` for an SRT, VTT, or ASS file or a non-empty `subtitles` list of
`subtitle_text`, `start_time`, and `end_time` cues. If both are supplied, the
subtitle file takes priority. The tool uses `vod:subtitle:add` in JWT mode and
runs in the background; pass the `task_id` and `operation="add_subtitles"`
from its background result to [`vod_get_task`](#vod_get_task).

Style options are `subtitle_pos_preset` (`bottom_center`, `top_center`,
`center`, `lower_third`), positive `subtitle_font_size`, RGBA
`subtitle_font_color` (`#RRGGBBAA`), and a documented MediaKit font identifier.
Optional `client_token` supports submission reconciliation; callback and queue
fields are also available. `project` is a legacy convenience-endpoint extension
and is omitted by default.

**Annotations:** `readOnlyHint=False`, `destructiveHint=False`,
`idempotentHint=False`, `openWorldHint=True`

## vod_remove_subtitles

Remove hardcoded dialogue subtitles or recognized on-screen text from a public
HTTPS video with MediaKit precision erasure. `mode="subtitle"` is the safe
default; `mode="text"` is broader and may remove titles, labels, or watermarks.
`output_encode_mode` selects `quality` (default) or `size`. Optional controls
include up to 20 normalized erasure rectangles, selected/skipped time segments,
subtitle OCR thresholds, callbacks, a queue ID, and a `client_token`. The
legacy `model_version` (`v4`/`v5`) and `project` extensions are omitted unless
explicitly supplied. The tool uses `vod:subtitle:remove` and runs in the
background; pass the `task_id` and `operation="remove_subtitles"` from its
background result to [`vod_get_task`](#vod_get_task).

**Annotations:** `readOnlyHint=False`, `destructiveHint=False`,
`idempotentHint=False`, `openWorldHint=True`

## vod_separate_audio

Submit an asynchronous BytePlus VOD AI MediaKit voice and background audio
separation task (`POST /api/v1/tools/separate-voice`). This tool is registered
when `BYTEPLUS_VOD_MEDIAKIT_API_KEY` is set and uses the `vod:extract` JWT
scope.

Input takes a public HTTPS `audio_url` or `video_url` (exactly one), a `scene`,
and an `output_format`.

**Annotations:** `readOnlyHint=False`, `destructiveHint=False`,
`idempotentHint=False`, `openWorldHint=True`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `audio_url` | string | No | Public HTTPS audio URL (mp3, m4a, wav). Exactly one of `audio_url`/`video_url` |
| `video_url` | string | No | Public HTTPS video URL (mp4, flv, ts, avi, mov, wmv, mkv). Exactly one of `audio_url`/`video_url` |
| `scene` | string | No | `Audio` (default), `Music`, `Drama`, `Narrate`. `Audio`/`Music` produce 2 tracks; `Drama`/`Narrate` produce 3 |
| `output_format` | string | No | `aac` (default), `mp3`, `wav`, `m4a`, `flac` |

### Output

Returns `VodSeparateAudioOutput` with `provider` `byteplus-vod-mediakit`,
`status` `accepted`, the provider `request_id` and `provider_log_id`, and a
heuristic `recommended_poll_after_ms`. Run it in the background, then pass the
`task_id` and `operation="separate_audio"` from its result to
[`vod_get_task`](#vod_get_task). The POST is non-idempotent and is never
retried automatically (timeout/5xx means ambiguous completion).

## vod_get_task

Poll the status and outputs of **any** BytePlus VOD AI MediaKit task: one
poller for every `vod_*` create tool. Pass the `task_id` and the `operation`
that the create tool returned. This tool is registered when
`BYTEPLUS_VOD_MEDIAKIT_API_KEY` is set and uses the `vod:read` JWT scope.

**Execution:** Optional background job. `persist_output` defaults to
`false`, so a plain foreground call is a quick status poll; background
execution is required only when `persist_output=true`.

**Annotations:** `readOnlyHint=False`, `destructiveHint=False`,
`idempotentHint=True`, `openWorldHint=False`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `task_id` | string | Yes | MediaKit task ID returned by a `vod_*` create tool (1-256 chars, `[A-Za-z0-9._:-]`) |
| `operation` | `"enhance"` \| `"transcode"` \| `"separate_audio"` \| `"add_subtitles"` \| `"remove_subtitles"` | Yes | Operation that created the task, echoed as `operation` by every `vod_*` create tool |
| `persist_output` | boolean | No | Copy finished outputs into durable artifact storage, once per task (default: **false**; background execution required when true) |
| `output_path` | string | No | Local file (or directory ending with `/`) for the output; **a directory is required for `separate_audio`**, which writes every track. stdio only, inside an output root, requires `persist_output=true`. See [local output paths](#writing-outputs-to-local-paths) |
| `overwrite` | boolean | No | Allow `output_path` to replace an existing file with different content (default: false) |

| `operation` | Create tool | Output roles |
|---|---|---|
| `enhance` | `vod_enhance_video` | `video` |
| `transcode` | `vod_transcode_video` | `video` |
| `add_subtitles` | `vod_add_subtitles` | `video` |
| `remove_subtitles` | `vod_remove_subtitles` | `video` |
| `separate_audio` | `vod_separate_audio` | `voice`, `background` (`Audio`/`Music` scenes) or `voice`, `music`, `sfx` (`Drama`/`Narrate` scenes) |

A wrong `operation` usually surfaces as a not-found provider error; the error
message then asks you to check that `operation` matches the create tool's
output.

### Output

Returns `VodTaskOutput`:

| Field | Description |
|---|---|
| `provider` | Always `byteplus-vod-mediakit` |
| `task_id`, `operation` | Echo of the polled task |
| `status` | Normalized `processing` (queued or running; poll again), `succeeded`, or `failed` |
| `provider_status`, `request_id` | Raw provider status label and diagnostic request ID, when returned |
| `created_at`, `finished_at` | ISO-8601 provider timestamps, when returned |
| `duration_seconds` | Output duration (input duration for `separate_audio`), when reported |
| `resolution`, `fps`, `video_codec`, `tool_version` | Output metadata, when reported (`fps` and `tool_version` for enhance; `video_codec` for transcode) |
| `outputs` | List of `TaskOutputFile`; empty until `succeeded` |
| `error` | `code` and safe `message` when `status="failed"` |

Each `TaskOutputFile` carries `role`, the expiring `source_url` (kept even
when persistence is skipped or fails), `source_expires_at`, `persistence`
(`not_requested`, `persisted`, or `failed`), a durable `artifact` when
persisted (with `local_path` when also exported), and a safe
`persistence_issue` when the copy failed. A failed copy never erases provider
success.

With `persist_output=true`, concurrent first polls share one artifact copy per
task output and each completed track is cached immediately. Cancelling while
a later audio track downloads preserves earlier tracks for subsequent polls.
Repeated polls reuse cached artifacts. Durable copies are capped by the media policy (200 MiB for
video). GET polling is automatically retried on retryable errors: 429, 5xx,
and poll timeouts or connection failures (a poll creates no provider state).

### Example

Foreground status poll (the default):

```json
{"task_id": "<task_id from vod_separate_audio>", "operation": "separate_audio"}
```

Once `status` is `succeeded`, submit through `ark_job_submit` to persist and
export every track:

```json
{
  "tool_name": "vod_get_task",
  "arguments": {
    "input": {
      "task_id": "<task_id>",
      "operation": "separate_audio",
      "persist_output": true,
      "output_path": "/Users/me/Videos/stems/"
    }
  }
}
```

**Polling workflow:** submit the create tool through `ark_job_submit`, read
`task_id` and `operation` from the job result, poll `vod_get_task` in the
foreground (the default) until `status` is not `processing`, then run it once
through `ark_job_submit` with `persist_output=true` to keep the outputs.
**Never resubmit the create tool to poll.**

## omnihuman_create_task / omnihuman_get_task / omnihuman_cancel_task

Generate a video of a person speaking or performing from one portrait image and
one audio clip with BytePlus OmniHuman 2.0 (`realman_avatar_picture_omni20_cv`).
The three tools are registered only when `BYTEPLUS_OMNIHUMAN_ENABLED=true` and
`BYTEPLUS_CV_API_KEY` is set (Bearer auth to the Vision AI host). They use their
own key, separate from the ModelArk key.

**Execution:** `omnihuman_create_task` is a required background job and
`omnihuman_get_task` supports optional background execution for video
persistence. `omnihuman_cancel_task` is a plain foreground tool.

### Input (`omnihuman_create_task`)

| Field | Type | Required | Description |
|---|---|---|---|
| `image` | string | Yes | Public HTTPS URL or `seed-media://artifacts/<id>`. jpeg/png/webp/bmp/tiff/gif/heic/heif, under 30 MB, aspect ratio 0.4-2.5, 300-6000 px per side |
| `audio` | string | Yes | Public HTTPS URL or `seed-media://artifacts/<id>`. wav or mp3, under 15 MB, 4-300 seconds |
| `prompt` | string | No | Scene, movement, or camera guidance (up to 2000 characters; zh, en, ja, ko, es-MX, id) |
| `watermark` | boolean | No | Provider watermark; defaults to `true` |

Artifact references need an object-storage artifact backend
(`ARTIFACT_BACKEND=object_storage`) so BytePlus can fetch a presigned URL
(valid one hour) minted for the calling principal. With the filesystem backend,
upload the file with `media_upload` and pass the presigned URL instead. The
server validates artifact type, MIME type, and size; for plain URLs it applies
the SSRF policy only, and BytePlus validates dimensions and duration.

### Output

- `omnihuman_create_task` returns `task_id`, `status="accepted"`, and
  `recommended_poll_after_ms`. Submission is never retried automatically.
- `omnihuman_get_task` returns `status` (`queued`, `running`, `succeeded`,
  `failed`, `canceled`, `expired`, `not_found`). On the first successful poll
  with `persist_output=true` it copies the provider video into a durable
  `seed-media://` artifact (`video`); `source_url` is kept for one hour.
  Supports `output_path` on stdio.
- `omnihuman_cancel_task` returns `canceled`. Only queued tasks can be
  canceled; a started task returns `canceled=false` with an explanation.
  Immediately after creation the provider can reject the new ID as invalid
  (`50200`); retry after a few seconds. Tasks usually start within seconds, so
  cancelation is only practical when the provider queue is busy.

**Audio failures surface after submission.** The provider does not validate audio
duration or content when the task is submitted (a 2-second clip is accepted); the
task fails during processing and `omnihuman_get_task` then returns `status="failed"`
with `error.code` and `error.message` carrying the provider's own reason, for example
`InvalidParameter: ... audio duration (seconds) ... must be greater than or equal to 4`.
That failure is permanent, so do not keep polling or retry it unchanged.

**Long audio:** the documented limit is 4-300 seconds and under 15 MB. A 300-second
WAV only fits at low sample rates (16 kHz mono is about 9.6 MB, 24 kHz mono about
14.4 MB); 44.1 kHz or 48 kHz WAV is 26-58 MB and is rejected for size. Use MP3
(300 s at 128 kbps is about 4.8 MB) or generate shorter clips. `media_upload`
accepts at most 10 MB of audio.

Tasks are retained by the provider for 12 hours. Auth scopes:
`omnihuman:create`, `omnihuman:read`, `omnihuman:cancel`.

## Private asset library (`ark_asset_*`)

Registered when `BYTEPLUS_MODELARK_ACCESS_KEY` and
`BYTEPLUS_MODELARK_SECRET_KEY` are set. They manage asset groups (one subject
per group), upload trusted media, and run real-person verification for
Dreamina Seedance Advanced Creation Rights. Full guide, workflows and limits:
[assets.md](assets.md).

| Tool | Purpose |
|---|---|
| `ark_asset_group_ensure` | Reuse or create the AIGC group named exactly `subject` (idempotent); concurrent calls are serialized within one server event loop |
| `ark_asset_group_list` | List groups (`group_type` `AIGC` default or `LivenessFace`), or read 1-20 groups of any type by `group_ids`; unreadable IDs come back in `errors` |
| `ark_asset_create` | Add 1–20 files of one subject to one group; waits for `Active` (background-job capable). `group_created_empty` flags a new subject group whose sources all failed |
| `ark_asset_list` | List assets with filters, or read 1-20 assets by `asset_ids` (IDs or `asset://` references); returns status, type, group and `asset_uri`, with unreadable IDs in `errors` |
| `ark_asset_update` | Rename an asset (`asset_id`), or rename / re-describe a group (`group_id`); exactly one target |
| `ark_asset_verification_start` / `ark_asset_verification_result` | Real-person liveness verification → `LivenessFace` group ID |
| `ark_asset_delete` | Delete one asset (`asset_id`) or group (`group_id`). Irreversible; only with `BYTEPLUS_MODELARK_ASSETS_ALLOW_DELETE=true` and `confirm: true`. Deleting a group deletes its assets, so a non-empty group is refused unless `cascade: true` |

Asset calls hold the shared `modelark-openapi` rate-limit slot only around
each provider request, so processing waits and verification polling never
block a Seedance asset preflight.

Seedream and Seed Audio reject `asset://` unless
`BYTEPLUS_MODELARK_ASSET_REFERENCE_MODE=resolve` (experimental), which sends
the asset's temporary download URL instead.

## seed_audio_generate

Generate full-scene audio through Seed Speech: one clip, or up to 5
independent variations in parallel.

**Execution:** Required background job. Submit it through `ark_job_submit`
(or native task augmentation) and read the completed generation from the
background result.

**Annotations:** `readOnlyHint=False`, `destructiveHint=False`,
`idempotentHint=False`, `openWorldHint=True`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `text_prompt` | string | No* | Text describing the audio (1-3000 chars) |
| `audio_references` | list[AudioReference] | No | Up to 3 audio references (speaker/url/base64). Base64 WAV preflight-checked against 30s limit. Mutually exclusive with `image_reference` |
| `image_reference` | ReferenceImageInput | No | Image reference (mutually exclusive with audio) |
| `output` | AudioOutputOptions | No | Format, sample rate, speech rate, pitch |
| `watermark` | AudioWatermarkOptions | No | AIGC watermark controls |
| `variations` | integer | No | Independent generations to run in parallel (1-5, default 1). Seed Audio has no seed, so variations differ by the model's sampling |
| `variation_prompts` | list[string] | No | One prompt per variation (each 1-3000 chars), overriding `text_prompt`; must have exactly `variations` entries |
| `persist` | boolean | No | Whether to persist output (default: true) |
| `output_path` | string | No | Local file, or directory ending with `/` (**required when `variations > 1`**); stdio only, requires `persist=true`. See [local output paths](#writing-outputs-to-local-paths) |
| `overwrite` | boolean | No | Allow `output_path` to replace an existing file (default: false) |

\* Either `text_prompt` or `variation_prompts` must be provided.

### Output

Returns a `SeedAudioGenerateOutput`. For a single clip it carries
`duration_seconds`, `billing_duration_seconds`, `artifact`, optional
`subtitle`, `request_id`, `provider_log_id`, and optional `source_url`.

**With `variations > 1`, `artifact`, `duration_seconds`, and
`billing_duration_seconds` are `null`** and `summary` (`VariationSummary`)
carries `total`, `succeeded`, `failed`, and per-variation results (artifact or
error).

If the billed audio cannot be stored, the call still succeeds: `artifact`
carries a `persistence_error` and is either the provider URL
(`id="provider-url"`) or, when the provider returned no URL, the inline bytes
in `fallback_data` (`id="inline-fallback"`, up to
`ARTIFACT_INLINE_FALLBACK_MAX_BYTES`). See
[Artifacts](artifacts.md#persistence-failures).

### Example

```json
{
  "text_prompt": "Hello, welcome to BytePlus."
}
```

Three takes written to a local directory:

```json
{
  "text_prompt": "A warm narrator says: welcome back to the show.",
  "variations": 3,
  "output_path": "/Users/me/Audio/takes/"
}
```

## seedream_generate_image

**The only Seedream tool**: text-to-image, image-to-image and reference
editing, point or box edits, editing PNGs while keeping their transparency, provider batches
(`max_images`), and parallel variations. Registered when
`BYTEPLUS_MODELARK_API_KEY` is set and uses the `seedream:generate` JWT scope.

**Execution:** Required background job. Submit it through `ark_job_submit` (or
native task augmentation).

**Annotations:** `readOnlyHint=False`, `destructiveHint=False`,
`idempotentHint=False`, `openWorldHint=True`

### Model selection and validation

- **`model` takes an exact configured ID** from `ark_list_models(kind='image')`;
  omit it to use `SEEDREAM_DEFAULT_MODEL`.
- **Model limits are checked at runtime.** Every violation (reference count,
  `output_format`, `size`, `max_images` without batch support,
  `prompt_optimization`, transparent background rules) is reported in one
  error that ends with `Call ark_list_models(kind='image') for per-model limits.`

Built-in family limits (`ark_list_models` is authoritative for your server):

| Family | Max reference images | `max_images` batch | `output_format` | `background="transparent"` |
|---|---|---|---|---|
| Seedream 5.0 Pro (default) | 10 | No (use `variations`) | `png`, `jpeg` | Yes |
| Seedream Lite | 14 | Yes | `png`, `jpeg` | No |
| Seedream 4.x | 14 | Yes | `jpeg` | No |

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `prompt` | string | No* | What to generate, or the edit instruction when `images` are given (1-4000 chars, including any point/bbox markup) |
| `images` | list[ReferenceImageInput] | No | Reference or source images; per-model maximum checked at runtime. Accepts URLs, Base64, and `asset://<asset_id>` |
| `point` | EditCoordinate | No | Edit mode: `{x, y}` in normalized `0..999` space on the **first** image. Requires `images` |
| `bbox` | EditBbox | No | Edit mode: `{x1, y1, x2, y2}` in normalized `0..999` space (`x1 <= x2`, `y1 <= y2`) on the **first** image. Requires `images` |
| `model` | string | No | Exact configured model ID; omit for the default |
| `size` | string | No | Image dimensions (e.g. `"2048x2048"`); validated against the model |
| `seed` | integer | No | `-1` to `2147483647`. For a single call it is sent as is. **With `variations > 1` it is the base seed**: omitted lets the provider randomize each variation, `-1` picks a random seed per variation (recorded in `summary`), and `N` uses `N`, `N+1`, `N+2`, ... |
| `max_images` | integer | No | Images from one provider call (1-15), batch-capable models only. **Cannot be combined with `variations > 1`** |
| `output_format` | `"png"` \| `"jpeg"` | No | Output format; per-model set checked at runtime |
| `response_format` | `"url"` \| `"b64_json"` | No | Provider response format |
| `watermark` | boolean | No | AIGC watermark |
| `prompt_optimization` | `"standard"` \| `"fast"` | No | Prompt optimization mode; 5.0 Pro defaults to `SEEDREAM_PROMPT_OPTIMIZATION_MODE` (`standard`) |
| `background` | `"opaque"` \| `"transparent"` | No | `transparent` edits an image and keeps its transparency. It needs a supported model (Seedream 5.0 Pro), **exactly one PNG input image that already has transparent pixels**, and `output_format` omitted or `png`. **It does not remove the background of an opaque image**: the provider rejects an input without transparent pixels (HTTP 400), and the server rejects a Base64 input whose `mime_type` is not `image/png` before calling the provider |
| `variations` | integer | No | Independent generations to run in parallel (1-10, default 1); partial failures are reported per variation |
| `variation_prompts` | list[string] | No | One prompt per variation, overriding `prompt`; must have exactly `variations` entries |
| `persist` | boolean | No | Whether to persist output (default: true) |
| `dry_run` | boolean | No | Validate and return the provider request bodies and a cost estimate without calling the provider or billing (default: false) |
| `output_path` | string | No | Local file, or directory ending with `/` (**required when `variations > 1` or `max_images > 1`**); stdio only, requires `persist=true`. See [local output paths](#writing-outputs-to-local-paths) |
| `overwrite` | boolean | No | Allow `output_path` to replace an existing file (default: false) |

\* Either `prompt` or `variation_prompts` must be provided.

With `point` or `bbox`, the tool prepends coordinate markup to every prompt
(`Image 1<bbox>x1 y1 x2 y2</bbox>` and/or `Image 1<point>x y</point>`), so
refer to the target as "Image 1" in the instruction.

### Output

Returns `SeedreamGenerateOutput`:

| Field | Description |
|---|---|
| `provider`, `model`, `created_at` | Provider (`byteplus-modelark`), model ID used, and ISO-8601 timestamp |
| `dry_run` | `true` when nothing was generated; see `preview` |
| `artifacts` | Images from a single call (`variations=1`); empty for variations and dry runs |
| `item_errors` | Per-image errors from a single provider batch |
| `usage` | Token usage of a single call; `null` for variations and dry runs |
| `summary` | `VariationSummary` when `variations > 1`: `total`, `succeeded`, `failed`, and per-variation `index`, `seed`, artifact or error |
| `preview` | Dry run only: `requests` (provider bodies, one per variation, with reference image URLs reduced to scheme and host and inline Base64 to its size) and `estimated_cost_usd` (a static estimate, not a provider quote) |

A storage failure does not fail the call. Each image degrades on its own:
URL outputs come back as `id="provider-url"` references to the temporary
provider URL, and `b64_json` outputs as `id="inline-fallback"` references with
the bytes in `fallback_data`, both with `persistence_error` set. Items that were
already stored are unaffected.

### Examples

Text-to-image:

```json
{"prompt": "A lighthouse on a cliff at golden hour, watercolor", "size": "2048x2048"}
```

Box edit of the first image:

```json
{
  "prompt": "Replace the object in the box with a red bicycle",
  "images": [{"kind": "url", "url": "https://cdn.example.com/street.png"}],
  "bbox": {"x1": 120, "y1": 400, "x2": 560, "y2": 900}
}
```

Edit an image with transparency on Seedream 5.0 Pro (the input PNG must
already have transparent pixels):

```json
{
  "prompt": "Turn the circle into a glossy ceramic button, keep the background transparent",
  "images": [{"kind": "url", "url": "https://cdn.example.com/circle-transparent.png"}],
  "background": "transparent",
  "output_format": "png"
}
```

Four variations with a deterministic base seed, previewed first:

```json
{
  "tool_name": "seedream_generate_image",
  "arguments": {
    "input": {
      "prompt": "Minimal poster of a fox, flat colors",
      "variations": 4,
      "seed": 1000,
      "dry_run": true
    }
  }
}
```

Drop `dry_run` (and add `output_path` ending in `/` on stdio) to generate.

## seedance_create_task

**One tool for every Seedance model**: 2.0 (and its Fast and Mini variants),
2.5, and the whitelist-only 2.5 Premium. Pick the model with `model`; set
`variations` to create several tasks in parallel; set `dry_run` to preview the
request and its cost without billing. Registered when
`BYTEPLUS_MODELARK_API_KEY` is set and uses the `seedance:create` JWT scope.

**Execution:** Required background job. Submit it through `ark_job_submit` (or
native task augmentation); a dry run finishes on the first `ark_job_get`. Poll
the returned task IDs with [`gen_task_get`](#gen_task_get) and **never
resubmit the create tool to poll**: each call starts new billed tasks.

**Annotations:** `readOnlyHint=False`, `destructiveHint=False`,
`idempotentHint=False`, `openWorldHint=True`

### Model selection and validation

- **`model` takes an exact configured ID** from `ark_list_models(kind='video')`,
  which also lists each model's limits. Omit it to use `SEEDANCE_DEFAULT_MODEL`
  (default `dreamina-seedance-2-5-260628`, Seedance 2.5).
- **Premium is just a model ID** (`dreamina-seedance-2-5-premium-260915` by
  default). It is configured only when
  `BYTEPLUS_MODELARK_SEEDANCE_2_5_PREMIUM_ENABLED=true`.
- **The input schema advertises the widest limits; the model's own limits are
  checked at runtime.** Every violation (resolution, ratio, duration, reference
  counts, priority and expiry ranges, Draft, audio-only input, task type,
  `output_format`) is reported in one error that ends with
  `Call ark_list_models(kind='video') for per-model limits.`

Built-in family limits (`ark_list_models` is authoritative for what your
server has configured):

| Family | Resolutions | Duration (s) | Max images / videos / audios | Draft (final resolution) | Audio-only input | `omni_reference_task_type` | `output_format` |
|---|---|---|---|---|---|---|---|
| Seedance 2.0 | 480p, 720p, 1080p, 4k | 4-15 | 9 / 3 / 3 | No | No | No | No |
| Seedance 2.0 Fast, 2.0 Mini | 480p, 720p | 4-15 | 9 / 3 / 3 | No | No | No | No |
| Seedance 2.5 | 480p, 720p, 1080p | 4-30 | 30 / 10 / 10 | Yes (1080p) | Yes | `auto`, `reference`, `edit`, `extend` | `mp4`, `mov` |
| Seedance 2.5 Premium (whitelist, flag-gated) | 480p, 720p, 1080p, 4k | 4-30 | 30 / 10 / 10 | Yes (4k) | Yes | `auto`, `reference`, `edit`, `extend` | `mp4`, `mov` |

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `prompt` | string | No | Text prompt (1-32,000 chars). Optional when media or `variation_prompts` are given |
| `images` | list[SeedanceImageInput] | No | Up to 30 (per-model maximum at runtime); roles `first_frame`, `last_frame`, `reference_image`. Each entry may be a plain URL string, `asset://<asset_id>`, or `{"url": ...}` (coerced to `role=reference_image`) |
| `videos` | list[SeedanceVideoInput] | No | Up to 10 reference videos (per-model maximum at runtime); URL string, `asset://<asset_id>`, or `{"url": ...}` |
| `audios` | list[SeedanceAudioInput] | No | Up to 10 reference audio clips (per-model maximum at runtime). Seedance 2.5 models accept audio as the only media input; 2.0 models need an image or video as well |
| `model` | string | No | Exact configured model ID from `ark_list_models(kind='video')`; omit for the default. With `draft_task_id`, omit it to reuse the Draft task's model |
| `resolution` | `"480p"` \| `"720p"` \| `"1080p"` \| `"4k"` | No | Output resolution; supported set checked per model. `draft=true` allows only `480p` (the default); `draft_task_id` allows only the model's Draft final resolution (the default) |
| `ratio` | `"16:9"` \| `"4:3"` \| `"1:1"` \| `"3:4"` \| `"9:16"` \| `"21:9"` \| `"adaptive"` | No | Aspect ratio; `adaptive` is the provider default. Edit, extend, first-frame, and first/last-frame requests on Seedance 2.5 require it omitted or `adaptive` |
| `duration` | integer | No | `-1` for auto, or 4 up to the model's maximum (15 for 2.0, 30 for 2.5). `edit` requires `-1` or omitted |
| `omni_reference_task_type` | `"auto"` \| `"reference"` \| `"edit"` \| `"extend"` | No | Seedance 2.5 models only. `edit`/`extend` need at least one reference video and `ratio` omitted or `adaptive`; `edit` also needs `duration` omitted or `-1` |
| `output_format` | `"mp4"` \| `"mov"` | No | Seedance 2.5 models only. `mov` uses H.264, YUV 4:4:4, and PCM audio for post-production; some players cannot play it |
| `generate_audio` | boolean | No | Generate an audio track |
| `watermark` | boolean | No | AIGC watermark (default: false) |
| `return_last_frame` | boolean | No | Return the last frame as a separate image output |
| `execution_expires_after` | integer | No | Seconds before an unfinished task expires (3600-259200) |
| `priority` | integer | No | Priority (0-9) |
| `safety_identifier` | string | No | Safety identifier (max 64 chars) |
| `draft` | boolean | No | Draft mode step 1 (Seedance 2.5 models): a 480p preview. Mutually exclusive with `draft_task_id`. See [Draft mode](#draft-mode) |
| `draft_task_id` | string | No | Draft mode step 2: render a succeeded Draft task as the final video. Requires `variations=1` |
| `variations` | integer | No | Tasks to create in parallel (1-5, default 1). **Each variation is a separate billed task**; partial failures are reported per variation |
| `variation_prompts` | list[string] | No | One prompt per variation (each 1-32,000 chars), overriding `prompt`; must have exactly `variations` entries |
| `dry_run` | boolean | No | Validate and return the provider request bodies and a cost estimate without submitting or billing (default: false) |

At least one of `prompt`, `images`, `videos`, or `audios` is required (except
with `draft_task_id`), and `variations > 1` needs `prompt` or
`variation_prompts`. Text-only input is supported for text-to-video.

Private asset library references (`asset://<asset_id>`, see
[assets.md](assets.md)) are passed through natively. Refer to them in the
prompt by position ("Image 1", "Video 1"), never by ID. With AK/SK configured,
a `GetAsset` preflight rejects `Processing` or `Failed` assets before billing.

#### Auto-locked parameters by task type

When the provider detects (or is hinted via `omni_reference_task_type`)
that the task is video editing, extension, or first/last-frame generation,
certain parameters are auto-derived from the input media:

| Task type | Aspect ratio | Duration |
|---|---|---|
| Video editing | Locked to input video | Locked to input video (±0.3s) |
| Video extension | Locked to input video | Set freely |
| First/last-frame | Locked to first image | Set freely |
| Text-to-video / reference | Set freely | Set freely (or `-1`) |

### Draft mode

Draft mode is a two-step flow for checking a video cheaply before paying for
the final render. Seedance 2.5 and 2.5 Premium support it; Seedance 2.0 models
do not.

1. Call `seedance_create_task` with `draft: true` and the usual prompt and
   media. The tool sends `draft=true` at `480p`, the only Draft resolution. The
   Draft is billed as a normal 480p video, and `gen_task_get` reports
   `draft: true`. `variations` may be greater than 1 to compare several Drafts.
2. Once the Draft task has `succeeded`, call `seedance_create_task` again with
   `draft_task_id` set to its task ID (and `variations=1`). The provider reuses
   the Draft task's model, prompt, media, duration, ratio, seed,
   `generate_audio`, and `omni_reference_task_type`, so those fields (and
   `draft`, even `false`) must be omitted; the provider rejects them even when
   they match. `resolution` may only be omitted or the model's Draft final
   resolution, which is also the default: `1080p` on Seedance 2.5 and `4k` on
   Seedance 2.5 Premium. The Premium `4k` final is this server's rule, not
   stated in the public guide; revisit it if the provider differs.
   `return_last_frame`, `watermark`, `execution_expires_after`, `priority`,
   `safety_identifier`, and `output_format` can be set again; omitted values
   use the model defaults, not the Draft's values. The final video is billed
   separately at its resolution.

Before submitting step 2, the tool checks that the caller owns the Draft task,
that it succeeded, that it was created in Draft mode, and that it is at most
7 days old. If `model` is omitted, the tool uses the Draft task's model; a
different `model` is rejected.

### Output

Returns `SeedanceCreateTaskOutput`, **one shape for a single task, variations,
and dry runs**:

| Field | Description |
|---|---|
| `model`, `family` | Resolved model ID and family |
| `dry_run` | `true` when nothing was submitted; see `preview` |
| `tasks` | One entry per requested variation, in order (empty for a dry run): `index` (0-based; 0 for a single request), `task_id` (poll with `gen_task_get`; for `draft=true`, pass it as `draft_task_id` once the Draft succeeds; `null` when submission failed), `request_id`, and `error` (`code`, `message`, `request_id`, `retryable`, `ambiguous_completion`, `phase`) |
| `submitted`, `failed` | Number of submissions the provider accepted and that failed |
| `recommended_poll_after_ms` | Suggested delay before the first `gen_task_get` poll; `null` for a dry run |
| `preview` | Dry run only: `requests` (provider request bodies, one per variation, with HTTPS media URLs reduced to scheme and host, inline Base64 reduced to its size, and `asset://` references kept) and `estimated_cost_usd` (a static estimate from the server's price table, not a provider quote) |

### Examples

Single Seedance 2.5 task (default model):

```json
{
  "prompt": "A paper boat drifts down a rain-soaked street at dusk",
  "resolution": "1080p",
  "duration": 8
}
```

Three variations on an explicit model, submitted as a background job:

```json
{
  "tool_name": "seedance_create_task",
  "arguments": {
    "input": {
      "model": "dreamina-seedance-2-0-260128",
      "variations": 3,
      "variation_prompts": [
        "The cat walks forward slowly through the garden",
        "The cat looks around curiously",
        "The cat jumps onto a stone wall"
      ],
      "images": ["https://cdn.example.com/cat.png"],
      "resolution": "720p",
      "duration": 5
    }
  }
}
```

Dry run (returns `preview`, bills nothing):

```json
{"prompt": "Slow dolly shot of a lighthouse at dawn", "duration": 20, "dry_run": true}
```

Draft step 2 (render the final video from a succeeded Draft):

```json
{"draft_task_id": "cgt-20261017-draft"}
```

## gen_task_get

Check the status and outputs of **any** ModelArk generation task: Seedance
video (2.0, 2.5, and 2.5 Premium), Hyper3D, and Hitem3d. Pass every task ID you
are waiting on in one call. Registered when `BYTEPLUS_MODELARK_API_KEY` is set
(not gated by the 3D flag) and uses the `gen:read` JWT scope.

The server makes one provider list call per page of 20 task IDs (at most
three), filtered by the requested IDs, and re-fetches individually only the
tasks missing from the list or reported succeeded without their primary
video/model-file URL; a last-frame URL alone is not sufficient.
Persistence uses a per-task single-flight cache. Later polls fill missing or
failed outputs without downloading cached successful outputs again. MOV
outputs retain `video/quicktime` and `.mov` filenames on directory export.

**Execution:** Optional background job; background execution is required when
`persist_output=true`.

**Annotations:** `readOnlyHint=False`, `destructiveHint=False`,
`idempotentHint=True`, `openWorldHint=False`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `task_ids` | list[string] | Yes | 1-50 ModelArk generation task IDs from `seedance_create_task`, `hyper3d_create_task`, or `hitem3d_create_task` (not Ark job IDs); duplicates are ignored |
| `persist_output` | boolean | No | Copy each succeeded task's outputs into durable storage, once per task (default: **false**) |
| `output_dir` | string | No | Local directory for each succeeded task's files (`<artifact_id>.<ext>`); stdio only, requires `persist_output=true`. See [local output paths](#writing-outputs-to-local-paths) |
| `overwrite` | boolean | No | Allow `output_dir` writes to replace existing files (default: false) |

### Output

Returns `GenTaskGetOutput`:

| Field | Description |
|---|---|
| `tasks` | One `GenTask` per found task, in request order |
| `errors` | Task IDs that could not be checked, each with `task_id`, `code` (`NOT_OWNED`, `NOT_FOUND`, or a provider code), and `message` |
| `all_terminal` | `true` when every found task has finished, so polling can stop |
| `active_tasks` | Number of found tasks still queued or running |
| `recommended_poll_after_ms` | Suggested delay before the next poll (5000 while any task is active); `null` when `all_terminal` |

Each `GenTask` contains:

| Field | Description |
|---|---|
| `task_id`, `model` | Provider task ID and the model that ran it |
| `family` | Registry family of the model; `null` when the model is not configured on this server |
| `kind` | `video`, `3d`, or `unknown`, from the registry binding for `model` |
| `status` | `queued`, `running`, `succeeded`, `failed`, `cancelled`, or `expired` |
| `created_at`, `updated_at` | ISO-8601 creation and last status-change times |
| `outputs` | List of `TaskOutputFile`: roles `video` and `last_frame` for video tasks, `model_file` for 3D tasks. Empty until the task succeeds |
| `error` | Provider failure `code` and `message` when failed |
| `usage` | Token usage reported by the provider |
| `draft` | `true` for a Seedance Draft task (480p preview); pass `task_id` as `draft_task_id` within 7 days. `null` for 3D |
| `settings` | Video generation settings echoed by the provider; `null` for 3D |
| `queue` | Server-derived queue timing for a queued or running video task (below); `null` once finished and for 3D |

Each `TaskOutputFile` carries `role`, the expiring provider `source_url`
(about 24 hours, kept even when persistence is skipped or fails),
`source_expires_at`, `persistence` (`not_requested`, `persisted`, or
`failed`), a durable `artifact` when persisted (with `local_path` when also
exported), and a safe `persistence_issue` when the copy failed. Re-persist a
failed output with `seed_media_persist_url` before its source URL expires.

Task IDs not created by the caller are reported in `errors` instead of failing
the call. A failure of the provider list call itself fails the whole call.

`queue` (`SeedanceQueueInfo`) is set only while the task is `queued` or
`running` and is `null` once it finishes. It is derived by this server from the
task's own timestamps, because ModelArk publishes no queue position or ETA:

| Field | Description |
|---|---|
| `queued_seconds` | Seconds queued: now minus `created_at` while queued, or `updated_at` minus `created_at` once running (approximate) |
| `running_seconds` | Seconds since the last status update while running (approximate) |
| `service_tier` | `default` or `flex`; flex tasks usually queue longer |
| `expires_at` | ISO-8601 time after which the provider fails an unfinished task: `created_at + execution_expires_after` |
| `expires_at_estimated` | `true` when the provider did not report `execution_expires_after` and the 48-hour default was assumed |
| `hint` | Human-readable summary to relay to the user |

### Example

Foreground status poll of three tasks:

```json
{"task_ids": ["cgt-20261017-aaaa", "cgt-20261017-bbbb", "cgt-20261017-cccc"]}
```

Once `all_terminal` is `true`, keep the outputs durably (and optionally write
them locally) through a background job:

```json
{
  "tool_name": "gen_task_get",
  "arguments": {
    "input": {
      "task_ids": ["cgt-20261017-aaaa", "cgt-20261017-bbbb"],
      "persist_output": true,
      "output_dir": "/Users/me/Videos/seedance/"
    }
  }
}
```

### Polling workflow

1. Submit a create tool (`seedance_create_task`, `hyper3d_create_task`, or
   `hitem3d_create_task`) through `ark_job_submit`, poll
   `ark_job_get`, and read the provider `task_id` (or every variation's task
   ID) from the job result.
2. Call `gen_task_get` in the foreground with all the task IDs and the default
   `persist_output=false`. `queued` or `running` is not a failure; wait
   `recommended_poll_after_ms` and poll again.
3. When `all_terminal` is `true`, call `gen_task_get` once more through
   `ark_job_submit` with `persist_output=true` before the ~24-hour source URLs
   expire.

**Never resubmit the create tool to poll**: that creates a second billed task.

## gen_task_list

List your ModelArk generation tasks (Seedance video and 3D), **newest first**,
from the last 7 days (the provider's list window). Registered when
`BYTEPLUS_MODELARK_API_KEY` is set and uses the `gen:read` JWT scope. Use
`gen_task_get` for outputs.

**Annotations:** `readOnlyHint=True`, `destructiveHint=False`,
`idempotentHint=True`, `openWorldHint=False`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `page_size` | integer | No | Maximum tasks to return (1-100, default 20) |
| `cursor` | string | No | Opaque `next_cursor` from a previous call made with the same filters |
| `status` | `queued` \| `running` \| `succeeded` \| `failed` \| `cancelled` \| `expired` | No | Only return tasks in this status |
| `model` | string | No | Only return tasks run by this exact model ID |
| `task_ids` | list[string] | No | Only return these task IDs (up to 100); IDs the caller does not own are skipped |

### Output

Returns `GenTaskPage` with `tasks` (newest first) and `next_cursor`. Each
summary has `task_id`, `model`, `kind` (`video`, `3d`, or `unknown`),
`status`, `created_at`, and `error` (when failed). Follow `next_cursor` until
it is `null`. **A page may hold fewer than `page_size` tasks while
`next_cursor` is non-null**, so keep paging.

**Cursors are bound to their filters.** Changing `status`, `model`,
`task_ids`, or `page_size` between pages rejects the cursor; repeat the
original filters or start again without a cursor.

**Ownership:** For the local stdio principal the tool lists the account's
tasks directly from the provider. For every other principal it pages the caller's owned task IDs from the ownership
store in slices of 100 and asks the provider only for those IDs, so the
provider request URL never overflows and other callers' tasks are never
listed.

## gen_task_cancel_or_delete

Cancel (queued) or delete (terminal) any ModelArk generation task: Seedance,
Hyper3D, or Hitem3d. Registered when `BYTEPLUS_MODELARK_API_KEY` is set and
uses the `gen:delete` JWT scope. A deleted task's outputs are no longer
retrievable through `gen_task_get`.

**Annotations:** `readOnlyHint=False`, `destructiveHint=True`,
`idempotentHint=False`, `openWorldHint=True`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `task_id` | string | Yes | ModelArk generation task ID |
| `mode` | "cancel" \| "delete" | Yes | Operation mode |
| `expected_status` | "queued" \| "succeeded" \| "failed" \| "expired" | Yes | Expected current status |
| `confirm` | true | Yes | Explicit confirmation required |

The handler fetches the current task state and rejects the operation if the
actual status does not match `expected_status`. This prevents accidental
cancellation of a running task.

### DELETE Semantics

| Current Status | Cancel | Delete |
|---|---|---|
| `queued` | Yes → `cancelled` | No |
| `running` | No | No |
| `succeeded` | No | Yes |
| `failed` | No | Yes |
| `expired` | No | Yes |
| `cancelled` | No | No |


## Parallel Variations

`seedream_generate_image`, `seed_audio_generate`, and `seedance_create_task`
take a `variations` field that generates multiple variations in a single call.
Each variation runs independently — partial failures are captured per
variation.

Variations have no individual wall-clock timeout, and time spent waiting for a
concurrency slot never counts against a variation. One batch deadline bounds
the whole call (see [Multi-Generation](multi-generation.md#deadlines-and-failure-phases)).
A variation stopped by the deadline or by a storage failure reports
`error.phase`: `queued` (never started, code `QUEUE_TIMEOUT`, safe to retry),
`generating` (provider call in flight, may have completed), or `persisting`
(output generated, storing it failed). Ordinary provider errors leave `phase`
null. Outputs that could not be stored but have a provider URL or fit inline
are returned as successful variations with `persistence_error` set.

### Seedream and Seed Audio variations

Variations are part of the base tools: set `variations` (and optionally
`variation_prompts`) on [`seedream_generate_image`](#seedream_generate_image)
(1-10; `seed` is the base seed) or [`seed_audio_generate`](#seed_audio_generate)
(1-5). The output's `summary` (`VariationSummary`) holds `total`,
`succeeded`, `failed`, and per-variation results (artifact or error); write
every persisted variation locally with `output_path` set to a directory ending
in `/`.

### Seedance variations

Seedance variations are part of [`seedance_create_task`](#seedance_create_task):
set `variations` (1-5) and optionally `variation_prompts`. Each variation is a
separate billed provider task. The background result lists every variation in
`tasks` (with `task_id` or `error`); poll all task IDs in one
`gen_task_get` call.

## seed_understand

Understand images and videos, or reason about a task, through the Seed 2.1
multimodal model. Deep thinking is always on; `reasoning_effort` (default
`medium`) sets its depth. The tool returns only the model's final answer: the
reasoning trace is discarded and never returned, logged, or saved. The default
model is `dola-seed-2-1-turbo-260628` (Seed 2.1 Turbo); `dola-seed-evolving` (the
latest Seed-series Pro-tier model) is also supported as a recognized built-in
ID — set `SEED_UNDERSTANDING_DEFAULT_MODEL=dola-seed-evolving` to opt in; its
family auto-resolves to `pro` so no explicit `SEED_UNDERSTANDING_MODEL_FAMILY`
is required. Other custom model IDs can be registered via
`SEED_UNDERSTANDING_MODEL_BINDINGS`.

**Execution:** Required background job. Submit through `ark_job_submit`, or
call the tool with native task metadata on a task-capable client; its direct
contract declares `execution.taskSupport="required"`. Both return a local job
ID before ModelArk finishes and use
the server-recommended two-second polling interval. Foreground direct calls are
rejected immediately. Choose a task TTL that covers the expected analysis
duration. The chat request timeout is `SEED_UNDERSTANDING_TIMEOUT_MS` (defaults
to `BYTEPLUS_REQUEST_TIMEOUT_MS`).

**Annotations:** `readOnlyHint=False` (because `save_to` writes local files),
`destructiveHint=False`, `idempotentHint=False`, `openWorldHint=True`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `prompt` | string | Yes | The question or task for the model to reason about (1-32,000 chars) |
| `images` | list[UnderstandingImageInput] | No | Images to understand (URL or Base64, max 32) |
| `videos` | list[UnderstandingVideoInput] | No | Videos to understand (URL only, max 32) |
| `system` | string | No | Optional system instruction (max 32,000 chars) |
| `model` | string | No | Override the configured Seed 2.1 model ID |
| `reasoning_effort` | "low" \| "medium" \| "high" | No | Depth of deep thinking (default: `medium`); always sent |
| `response_format` | UnderstandingResponseFormat | No | Constrain the answer to JSON or a JSON Schema, enforced by the provider during generation |
| `json_retry` | integer | No | Extra attempts (0-2, default 0) when a JSON answer fails to parse or validate; each is a new billed completion |
| `save_to` | string | No | Absolute file path (stdio only, inside an output root) where the answer is written; validated before the model is called |
| `overwrite` | boolean | No | Allow `save_to` to replace an existing file with different content (default: false) |
| `return_content` | "full" \| "summary" \| "none" | No | How much of the answer to inline (default: `full`; `summary` is the first 2,000 characters) |
| `temperature` | float | No | Sampling temperature (0.0-2.0) |
| `max_tokens` | integer | No | Maximum output tokens (1-32,768), including deep-thinking tokens |
| `top_p` | float | No | Nucleus sampling probability (0.0-1.0) |
| `repetition_penalty` | float | No | Repetition penalty (0.0-2.0). Ark-only parameter. |
| `thinking` | boolean | No | **Deprecated, ignored.** `thinking=false` only logs a warning; it will be removed |

**UnderstandingImageInput** and **UnderstandingVideoInput** are `MediaSource`
subclasses with the appropriate MIME validation and size limits. Video Base64
is not supported by the chat endpoint — upload local videos via `media_upload`
first to get an HTTPS URL.

**UnderstandingResponseFormat:**

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | "text" \| "json_object" \| "json_schema" | Yes | `json_object` requires any valid JSON object; `json_schema` requires JSON that satisfies `json_schema` |
| `json_schema.name` | string | With `json_schema` | Schema name (1-64 chars: letters, digits, `_`, `-`) |
| `json_schema.schema` | object | With `json_schema` | JSON Schema (draft 2020-12), at most 64 KB serialized; checked for validity before the call |
| `json_schema.description` | string | No | Optional description (max 2,000 chars) |
| `json_schema.strict` | boolean | No | Ask the provider to enforce the schema strictly (default: true) |

The server also parses the answer and, for `json_schema`, validates it locally.
When that fails and `json_retry > 0`, the prompt is re-sent with the violation
appended. A retry is never attempted when the answer was cut off by
`max_tokens` (`finish_reason="length"`); raise `max_tokens` instead.

`save_to` writes pretty-printed JSON when the answer parsed as JSON, otherwise
UTF-8 text. It follows the [local output path](#writing-outputs-to-local-paths)
rules. If the write fails after the completion was billed, the call still
succeeds, `saved_path` stays null, and the full answer is returned inline even
when `return_content` was `summary` or `none`.

### Output

Returns `SeedUnderstandOutput`:

| Field | Description |
|---|---|
| `model`, `completion_id`, `request_id` | Model used and provider IDs for tracing |
| `choices` | Completion choices; see below |
| `usage` | `prompt_tokens`, `completion_tokens` (including reasoning), `total_tokens`, and `reasoning_tokens` when the provider reports it — summed over all attempts |
| `attempts` | Completions requested: 1 plus any `json_retry` attempts used |
| `saved_path`, `saved_bytes` | Where `save_to` wrote the answer and its size, when set |

Each choice has `content` (the final answer, truncated for `summary`, empty for
`none`), `content_chars` (full answer length), `content_truncated`, `parsed`
(the answer as JSON when a JSON `response_format` parsed and validated —
returned even with `summary` or `none`), `schema_violation` (`path`, `message`,
`finish_reason`, when the JSON did not parse or validate), and
`finish_reason`. There is no `reasoning_content` field.

Chat timeouts are reported as `TIMEOUT` with `retryable=true` and
`ambiguous_completion=false`, but they are **not** retried automatically,
because a second full-length thinking run doubles latency and token cost.
429 and 5xx responses are still retried.

### Example

```json
{
  "prompt": "Describe what happens in this video and identify the objects in this image.",
  "videos": [{"kind": "url", "url": "https://.../sample.mp4", "mime_type": "video/mp4"}],
  "images": [{"kind": "url", "url": "https://.../frame.png", "mime_type": "image/png"}],
  "reasoning_effort": "medium",
  "max_tokens": 4096
}
```

Structured review written straight to disk:

```json
{
  "prompt": "Review this ad against the template and return the beats.",
  "videos": [{"kind": "url", "url": "https://.../ad.mp4", "mime_type": "video/mp4"}],
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "ad_review",
      "schema": {
        "type": "object",
        "properties": {"beats": {"type": "array", "items": {"type": "string"}}},
        "required": ["beats"]
      }
    }
  },
  "json_retry": 1,
  "save_to": "/Users/me/project/reviews/ad.json",
  "return_content": "none"
}
```

## seed_audio_understand

Understand, transcribe, translate, or reason about audio clips (speech, music,
or sound) through a ModelArk chat model that accepts audio input. Typical uses:
transcription, speech translation, speaker or emotion analysis, summaries,
meeting minutes, and questions about what is heard. For long-form transcription
with word-level timings, use [`speech_to_text`](#speech_to_text).

The model is configured by `SEED_AUDIO_UNDERSTANDING_MODEL` (default
`seed-2-0-lite-260428`); it is not a per-call argument. Any ModelArk model that
accepts Chat Completions `input_audio` parts with deep thinking can be swapped in
without a code change. Deep thinking is always on and only the final answer is
returned, exactly as with `seed_understand`.

**Execution:** Required background job, same as `seed_understand`: submit through
`ark_job_submit` (or native task metadata) and poll. Scope `understanding:read`;
timeout `SEED_UNDERSTANDING_TIMEOUT_MS`.

**Annotations:** `readOnlyHint=False` (because `save_to` writes local files),
`destructiveHint=False`, `idempotentHint=False`, `openWorldHint=True`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `prompt` | string | Yes | The question or task about the audio (1-32,000 chars) |
| `audios` | list[UnderstandingAudioInput] | Yes | Audio clips (1-8), sent to the model in order |
| `system`, `reasoning_effort`, `response_format`, `json_retry`, `save_to`, `overwrite`, `return_content`, `temperature`, `max_tokens`, `top_p`, `repetition_penalty` | — | No | Same as [`seed_understand`](#seed_understand) |

**UnderstandingAudioInput** is a `MediaSource` (`kind`, `url`, `data`,
`mime_type`):

- `kind="url"` — an HTTPS URL; the provider fetches and decodes it. For local
  files, upload with `media_upload` first.
- `kind="base64"` — raw Base64 (no `data:` prefix), at most 10 MB decoded.
  `mime_type` is required: `audio/wav`, `audio/mpeg`, `audio/flac`,
  `audio/aac`, or `audio/mp4` (m4a), plus their `x-` aliases. The provider
  rejects Base64 Ogg/Opus and raw PCM; pass those as URLs.

With `seed-2-0-lite-260428`, `json_schema` is enforced by the provider but
`json_object` is not, so use `json_schema` (or `json_retry`) when you need JSON.

### Output

Returns `SeedAudioUnderstandOutput`, identical in shape to
`SeedUnderstandOutput`. `model` is the configured audio model.

### Example

```json
{
  "prompt": "Summarize this call and list action items with owners.",
  "audios": [{"kind": "url", "url": "https://.../call.m4a"}],
  "reasoning_effort": "medium",
  "save_to": "/Users/me/project/notes/call-summary.md"
}
```

## speech_to_text

Transcribe audio to text via Seed Speech ASR. Submits audio via HTTP and polls
internally until transcription is complete.

**Execution:** Required background job. The local MCP task or Ark job ID
protects the provider polling window, which can run for up to the configured
600-second default. Its background result contains the complete
`TranscriptionResult`. There is no separate provider task tool or object-storage
upload requirement.

**Annotations:** `readOnlyHint=True`, `destructiveHint=False`,
`idempotentHint=True`, `openWorldHint=False`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `audio` | AsrAudioInput | Yes | Audio source (see below) |
| `options` | AsrRequestOptions | No | Transcription feature toggles (see below) |

**AsrAudioInput** — provide exactly one source:

| Field | Type | Required | Description |
|---|---|---|---|
| `audio_url` | string | No\* | HTTPS URL of the audio file |
| `audio_data` | string | No\* | Base64-encoded audio bytes |
| `audio_file_path` | string | No\* | Absolute local file path (stdio transport only) |
| `audio_format` | `wav` \| `mp3` \| `ogg` \| `raw` \| `flac` | Yes | Audio format |

\* Provide exactly one of `audio_url`, `audio_data`, or `audio_file_path`.

**AsrRequestOptions:**

| Field | Type | Required | Description |
|---|---|---|---|
| `language` | string | No | BCP-47 language code. Default: `en-US` |
| `enable_punc` | boolean | No | Enable automatic punctuation |
| `enable_itn` | boolean | No | Enable inverse text normalization (number formatting) |

### Output

Returns `SpeechToTextOutput` with `result` (`TranscriptionResult`) and
optional `log_id`.

**TranscriptionResult:**

| Field | Type | Description |
|---|---|---|
| `text` | string | Full transcript text |
| `utterances` | list[TranscriptionUtterance] | Utterance-level segments with timestamps |
| `duration_ms` | integer | Total audio duration in milliseconds |

**TranscriptionUtterance:**

| Field | Type | Description |
|---|---|---|
| `text` | string | Utterance text |
| `start_time_ms` | integer | Start time in milliseconds |
| `end_time_ms` | integer | End time in milliseconds |
| `words` | list[TranscriptionWord] | Word-level detail |
| `speaker_id` | string | Speaker label (if diarization is enabled) |
| `channel_id` | string | Audio channel (if channel split is enabled) |

**TranscriptionWord:**

| Field | Type | Description |
|---|---|---|
| `text` | string | Word text |
| `confidence` | float | Recognition confidence (0.0–1.0) |
| `start_time_ms` | integer | Start time in milliseconds |
| `end_time_ms` | integer | End time in milliseconds |

### Example

```json
{
  "audio": {
    "audio_url": "https://example.com/meeting.wav",
    "audio_format": "wav"
  },
  "options": {
    "language": "en-US",
    "enable_punc": true,
    "enable_itn": true
  }
}
```

## media_upload

Upload 1-50 image, audio, or video files to object storage (TOS or S3) and
receive a presigned HTTPS URL for each. Registered with object storage and
uses the `media:upload` JWT scope.

**Execution:** Required background job (use `ark_job_submit`), because one
item can hold up to 200 MiB of video.

**Annotations:** `readOnlyHint=False`, `destructiveHint=False`,
`idempotentHint=False`, `openWorldHint=True`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `items` | list[MediaUploadItem] | Yes | 1-50 files to upload |
| `expires_in_seconds` | integer | No | Presigned URL validity (60–604800) applied to every item. Defaults to the configured TTL; use a long value (e.g. 3600) for VOD inputs that are fetched asynchronously |
| `max_concurrent` | integer | No | Uploads running at the same time (1-8, default 4) |

**MediaUploadItem:**

| Field | Type | Required | Description |
|---|---|---|---|
| `media_type` | "image" \| "audio" \| "video" | Yes | Media category for MIME and size validation |
| `mime_type` | string | No* | MIME type such as `video/mp4`; optional for `file_path` items (guessed from the file extension), required for `data` items |
| `data` | string | No** | Base64-encoded media bytes |
| `file_path` | string | No** | Absolute local path; stdio transport only |
| `key_prefix` | string | No | Optional object key prefix (default `references`; letters, digits, `-`, `_`, `/`) |

\* A `file_path` item whose MIME type cannot be guessed fails on its own and
asks for `mime_type`. \*\* Provide exactly one of `data` or `file_path` per
item.

Every item is validated, and the total decoded size is checked against
`MEDIA_UPLOAD_BATCH_MAX_BYTES` (default 500 MiB), before any upload starts.
**An oversized request fails as a whole**; split it into smaller calls.

### Output

Returns `MediaUploadOutput`:

| Field | Description |
|---|---|
| `items` | Per-item results in request order: `index`, `file_path` (as given), `url`, `object_key` (reusable with `media_presign`), `expires_at`, `mime_type`, `bytes`, and on failure `error` and `retryable` |
| `succeeded`, `failed` | Number of items uploaded and failed |
| `total_bytes` | Bytes uploaded across successful items |

**Items fail independently** (validation, missing file, or provider error)
without failing the call, so always check `failed` and `items[].error`.

### Example

```json
{
  "tool_name": "media_upload",
  "arguments": {
    "input": {
      "items": [
        {"media_type": "video", "file_path": "/Users/me/clips/reference.mp4"},
        {"media_type": "image", "mime_type": "image/png", "data": "<base64>"}
      ],
      "expires_in_seconds": 3600
    }
  }
}
```

## media_presign

Generate fresh presigned HTTPS GET URLs for 1-100 existing objects without
re-uploading. Use it when a previously uploaded reference's URL has expired
or is about to expire. Registered with object storage and uses the
`media:presign` JWT scope.

**Annotations:** `readOnlyHint=True`, `destructiveHint=False`,
`idempotentHint=True`, `openWorldHint=False`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `object_keys` | list[string] | Yes | Object keys returned by prior `media_upload` calls (1–100) |
| `expires_in_seconds` | integer | No | Presigned URL validity (60–604800) applied to every key. Defaults to the configured TTL; use a long value (e.g. 3600) for VOD inputs |

### Output

Returns `MediaPresignOutput` with `items` in input order (each with
`object_key`, `url`, `expires_at`, and on failure `code`, `error`, and
`request_id`), plus `succeeded` and `failed`. **Failures are per key**:
`INVALID_KEY` (malformed key), `NOT_OWNED` (not uploaded by the caller),
`INTERNAL`, or a provider error code. Check `failed`.

## hyper3d_create_task / hitem3d_create_task

Create an asynchronous 3D generation task. Both tools are gated by
`BYTEPLUS_MODELARK_3D_ENABLED=true` (disabled by default) and reuse the
ModelArk API key.

**Execution:** Required background job for provider submission. Read the
provider task ID from its background result.

- `hyper3d_create_task` (Hyper3d-Gen2): text-to-3D and/or image-to-3D
  (1-5 images). Exposes `seed`, `callback_url`, and model text-command
  parameters (`material`, `mesh_mode`, `quality_override`, `addons`,
  `use_original_alpha`, `bbox_condition`, `ta_pose`, `subdivision_level`,
  `file_format`, `hd_texture`).
- `hitem3d_create_task` (Hitem3d-2.0): image-to-3D only (1-4 images).
  Exposes `callback_url` and model text-command parameters (`resolution`,
  `face`, `file_format`, `request_type`, `multi_images_bit`).

### Output

Returns `Seed3DCreateTaskOutput` with `task_id`, `status="queued"`, and a
recommended polling delay.

### Polling, listing, and cancelling 3D tasks

3D tasks share the ModelArk generation task lifecycle with Seedance:

- **Poll** with [`gen_task_get`](#gen_task_get). A 3D task reports
  `kind="3d"` and one `model_file` output (usually a zip) once it succeeds.
  Poll in the foreground with `persist_output=false`, then call it through
  `ark_job_submit` with `persist_output=true` (and optionally `output_dir`) to
  copy the file into durable storage (`seed-media://artifacts/{id}`).
- **List** with [`gen_task_list`](#gen_task_list) (last 7 days; filter by
  `model` to see only Hyper3D or Hitem3d tasks).
- **Cancel or delete** with
  [`gen_task_cancel_or_delete`](#gen_task_cancel_or_delete).
