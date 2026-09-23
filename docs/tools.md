# Tools Reference

The server exposes a conditional set of typed tools. `seed_media_get_artifact`,
`seed_media_export_artifact`, and `seed_media_persist_url` are always
available, provider tools are registered only when their credentials
are configured, and `media_upload`, `media_upload_batch`, `media_presign`, and
`media_presign_batch` are registered when object storage credentials (TOS or
S3) are present. Each
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

- `media_upload`
- `media_upload_batch`
- `seed_audio_generate`
- `seed_audio_generate_variations`
- `speech_to_text`
- `seedream_generate_image`
- `seedream_edit_image`
- `seedream_generate_image_variations`
- `seedance_create_task`
- `seedance_create_task_variations`
- `seedance_2_5_create_task`
- `seedance_2_5_create_task_variations`
- `hyper3d_create_task`
- `hitem3d_create_task`
- `seed_understand`
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
result contains the provider task ID used by the corresponding get tool.

The following retrieval tools advertise `execution.taskSupport="optional"`:

- `seedance_get_task`
- `seedance_get_tasks`
- `seed_media_persist_url`
- `hyper3d_get_task`
- `hitem3d_get_task`
- `vod_get_enhancement_task`
- `vod_get_transcode_task`
- `vod_get_audio_separation`
- `vod_get_subtitle_addition_task`
- `vod_get_subtitle_removal_task`

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
| `seedream_generate_image` | `output_path` | Must be a directory (ending with `/`) when `max_images > 1` |
| `seedream_edit_image` | `output_path` | Extra images get `-2`, `-3` suffixes when a file path is given |
| `seed_audio_generate` | `output_path` | |
| `seedance_get_task` | `output_path` | The last frame is written beside the video with a `-last-frame` suffix |
| `hyper3d_get_task`, `hitem3d_get_task` | `output_path` | |
| `vod_get_enhancement_task`, `vod_get_transcode_task`, `vod_get_audio_separation`, `vod_get_subtitle_addition_task`, `vod_get_subtitle_removal_task` | `output_path` | |
| `seedream_generate_image_variations`, `seed_audio_generate_variations`, `seedance_get_tasks` | `output_dir` | One file per artifact |
| `seed_understand` | `save_to` | Writes the final answer, not media |

`output_path` names a file, or a directory when it ends with `/`; files written
into a directory are named `<artifact_id>.<ext>`. Every tool that has one of
these fields also has `overwrite` (default `false`). The rules are the same
everywhere:

- **stdio only.** The fields are rejected on HTTP transport.
- **Absolute paths inside an allowed output root.** The roots are the client's
  MCP roots (`roots/list`) when the client provides them, otherwise
  `OUTPUT_ROOTS` (comma-separated absolute directories). With neither, path
  writing is disabled and the field is rejected.
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
| `scene` | `"common"` | No | Fixed current scene profile |
| `tool_version` | `"professional"` | No | Fixed current enhancement profile |
| `resolution` | `"4k"` | No | Fixed current output resolution |
| `bitrate_level` | `"high"` | No | Fixed current bitrate profile |
| `fps` | `24` | No | Fixed current frame rate in frames per second |
| `project` | string | No | Defaults to `default`; serialized upstream as `Project` |
| `input_duration_seconds` | number | No | Reserved for future pricing support; currently produces no estimate |
| `persist` | boolean | No | Best-effort artifact copy (default: true) |

### Output and execution limits

The verified provider contract returns `status="accepted"` with a task ID for
`vod_get_enhancement_task`. The non-idempotent POST is not retried automatically
because a timeout can have ambiguous completion. A completed output always
preserves `source_url`. Persistence is reported as `not_applicable`, `persisted`,
`failed`, or `not_requested`, and a failed artifact copy does not erase provider success.
Durable video copies remain subject to the 200 MiB limit.

The success-body mapping remains provisional and rejects unknown response
shapes. `estimated_cost_usd` is always null until convenience-endpoint pricing
is confirmed.

## vod_get_enhancement_task

Poll the status and retrieve the output of a BytePlus VOD AI MediaKit enhancement
task. This tool is registered only when `BYTEPLUS_VOD_MEDIAKIT_API_KEY` is set
and uses the `vod:read` JWT scope.

**Annotations:** `readOnlyHint=False`, `destructiveHint=False`,
`idempotentHint=True`, `openWorldHint=False`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `task_id` | string | Yes | Provider task ID from the background result of `vod_enhance_video` |
| `persist_output` | boolean | No | Persist the completed output on first successful poll (default: true; background execution required when true) |
| `output_path` | string | No | Local file (or directory ending with `/`) for the enhanced video; stdio only, inside an output root, requires `persist_output=true`. See [local output paths](#writing-outputs-to-local-paths) |
| `overwrite` | boolean | No | Allow `output_path` to replace an existing file with different content (default: false) |

### Output and execution limits

Returns `VodEnhancementTaskOutput` with normalized `processing`, `succeeded`, or
`failed` status. A completed result includes `source_url`, its 24-hour expiry,
duration, resolution, frame rate, enhancement tier, and provider timestamps.
With `persist_output=true`, concurrent first polls share one artifact copy and
the result is cached by task ID for later reuse. Cache failures emit safe
warnings but preserve any created artifact and the provider success. Artifact
copy failures are reported separately; durable video copies remain capped at
200 MiB.

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

Returns `VodTranscodeVideoOutput` with `status="accepted"` plus the `task_id`
to poll via `vod_get_transcode_task` and a heuristic `recommended_poll_after_ms`.
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

## vod_get_transcode_task

Poll the status and output of a BytePlus VOD AI MediaKit transcode task. This
tool is registered only when `BYTEPLUS_VOD_MEDIAKIT_API_KEY` is set and uses the
`vod:read` JWT scope.

**Annotations:** `readOnlyHint=False`, `destructiveHint=False`,
`idempotentHint=True`, `openWorldHint=False`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `task_id` | string | Yes | Provider task ID from the background result of `vod_transcode_video` |
| `persist_output` | boolean | No | Persist the completed output on first successful poll (default: true; background execution required when true) |
| `output_path` | string | No | Local file (or directory ending with `/`) for the transcoded video; stdio only, inside an output root, requires `persist_output=true`. See [local output paths](#writing-outputs-to-local-paths) |
| `overwrite` | boolean | No | Allow `output_path` to replace an existing file with different content (default: false) |

### Output and execution limits

Returns `VodTranscodeTaskOutput` with a normalized `status` of `processing`,
`succeeded`, or `failed` (the provider documents only `running`/`completed`/
`failed`). On success, `source_url` (24-hour lifetime) and optional metadata
(`duration_seconds`, `resolution`, `video_codec`) are returned; when
`persist_output=true` the output is copied once into the durable artifact store
and cached by task ID so repeated polls do not re-download. Persistence is
reported as `not_applicable`, `not_requested`, `persisted`, or `failed`, and a
failed artifact copy does not erase provider success. Durable video copies
remain subject to the 200 MiB limit. On failure, `error.code`/`error.message`
carry the safe provider failure detail. GET polling is automatically retried
on retryable errors: 429, 5xx, and poll timeouts or connection failures (a
poll creates no provider state).

## vod_add_subtitles

Burn subtitles into a public HTTPS video with MediaKit. Supply either
`subtitle_url` for an SRT, VTT, or ASS file or a non-empty `subtitles` list of
`subtitle_text`, `start_time`, and `end_time` cues. If both are supplied, the
subtitle file takes priority. The tool uses `vod:subtitle:add` in JWT mode and
runs in the background; pass the provider task ID from its background result to
`vod_get_subtitle_addition_task`.

Style options are `subtitle_pos_preset` (`bottom_center`, `top_center`,
`center`, `lower_third`), positive `subtitle_font_size`, RGBA
`subtitle_font_color` (`#RRGGBBAA`), and a documented MediaKit font identifier.
Optional `client_token` supports submission reconciliation; callback and queue
fields are also available. `project` is a legacy convenience-endpoint extension
and is omitted by default.

**Annotations:** `readOnlyHint=False`, `destructiveHint=False`,
`idempotentHint=False`, `openWorldHint=True`

## vod_get_subtitle_addition_task

Poll the provider task ID returned in the background result of
`vod_add_subtitles`. The tool uses `vod:read`, maps provider lifecycle states to
`processing`, `succeeded`, or `failed`, and can best-effort persist the MP4
output when `persist_output=true` (default). A succeeded response always
preserves the expiring `source_url`; persistence failure is reported separately
and does not erase provider success. Persistence requires background execution;
use `persist_output=false` for a foreground status check. With
`persist_output=true`, `output_path` (plus `overwrite`) also writes the video
to a [local path](#writing-outputs-to-local-paths).

**Annotations:** `readOnlyHint=False`, `destructiveHint=False`,
`idempotentHint=True`, `openWorldHint=False`

## vod_remove_subtitles

Remove hardcoded dialogue subtitles or recognized on-screen text from a public
HTTPS video with MediaKit precision erasure. `mode="subtitle"` is the safe
default; `mode="text"` is broader and may remove titles, labels, or watermarks.
`output_encode_mode` selects `quality` (default) or `size`. Optional controls
include up to 20 normalized erasure rectangles, selected/skipped time segments,
subtitle OCR thresholds, callbacks, a queue ID, and a `client_token`. The
legacy `model_version` (`v4`/`v5`) and `project` extensions are omitted unless
explicitly supplied. The tool uses `vod:subtitle:remove` and runs in the
background; pass the provider task ID from its background result to
`vod_get_subtitle_removal_task`.

**Annotations:** `readOnlyHint=False`, `destructiveHint=False`,
`idempotentHint=False`, `openWorldHint=True`

## vod_get_subtitle_removal_task

Poll the provider task ID returned in the background result of
`vod_remove_subtitles`. Its normalized lifecycle, optional durable MP4
persistence, source-URL preservation, and failure behavior match
`vod_get_subtitle_addition_task`, including the optional `output_path` and
`overwrite` fields. The tool uses `vod:read`; persistence requires
background execution, while `persist_output=false` allows a foreground
status check.

**Annotations:** `readOnlyHint=False`, `destructiveHint=False`,
`idempotentHint=True`, `openWorldHint=False`

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
provider task ID from its result to
`vod_get_audio_separation`. The POST is non-idempotent and is never retried
automatically (timeout/5xx means ambiguous completion).

## vod_get_audio_separation

Poll a BytePlus VOD AI MediaKit separate-voice task (`GET
/api/v1/tasks/{task_id}`). This tool is registered when
`BYTEPLUS_VOD_MEDIAKIT_API_KEY` is set and uses the `vod:read` JWT scope.

**Annotations:** `readOnlyHint=False`, `destructiveHint=False`,
`idempotentHint=True`, `openWorldHint=False`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `task_id` | string | Yes | Provider task ID from the background result of `vod_separate_audio` |
| `persist_output` | boolean | No | Copy completed tracks into durable artifact storage on first successful poll (default `true`; background execution required when true) |
| `output_path` | string | No | Local file (or directory ending with `/`) for the separated audio track; stdio only, inside an output root, requires `persist_output=true`. See [local output paths](#writing-outputs-to-local-paths) |
| `overwrite` | boolean | No | Allow `output_path` to replace an existing file with different content (default: false) |

### Output and execution limits

Returns `VodAudioSeparationTaskOutput` with a normalized `status` of
`processing`, `succeeded`, or `failed`. On success, `voice`, `background`,
`music`, and `sfx` each carry the track's expiring `source_url` (valid 24
hours) and, when best-effort persistence succeeds, a durable `artifact`
reference. `persist_output=true` copies each track once and caches it by task
ID so repeated polls do not re-download; persistence is reported per track as
`not_requested`, `persisted`, or `failed`, and a failed copy does not erase
provider success. On failure, `error.code`/`error.message` carry the safe
provider detail. GET polling is automatically retried on retryable errors:
429, 5xx, and poll timeouts or connection failures (a poll creates no provider
state).

## seed_audio_generate

Generate full-scene audio through Seed Speech.

**Execution:** Required background job. Retrieve the completed generation from
the background result.

**Annotations:** `readOnlyHint=False`, `destructiveHint=False`,
`idempotentHint=False`, `openWorldHint=True`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `text_prompt` | string | Yes | Text to synthesize (1-3000 chars) |
| `audio_references` | list[AudioReference] | No | Up to 3 audio references (speaker/url/base64). Base64 WAV preflight-checked against 30s limit. |
| `image_reference` | MediaSource | No | Image reference (mutually exclusive with audio) |
| `output` | AudioOutputOptions | No | Format, sample rate, speech rate, pitch |
| `watermark` | AudioWatermarkOptions | No | AIGC watermark controls |
| `persist` | boolean | No | Whether to persist output (default: true) |
| `output_path` | string | No | Local file (or directory ending with `/`) for the audio; stdio only, requires `persist=true`. See [local output paths](#writing-outputs-to-local-paths) |
| `overwrite` | boolean | No | Allow `output_path` to replace an existing file (default: false) |

### Output

Returns a `SeedAudioGenerateOutput` with `duration_seconds`,
`billing_duration_seconds`, `artifact`, optional `subtitle`, `request_id`,
`provider_log_id`, and optional `source_url`.

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

## seedream_generate_image

Generate or edit an image through ModelArk Seedream.

**Annotations:** `readOnlyHint=False`, `destructiveHint=False`,
`idempotentHint=False`, `openWorldHint=True`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `prompt` | string | Yes | Text prompt for image generation |
| `images` | list[MediaSource] | No | Reference images for editing |
| `model` | string | No | Override configured model ID |
| `size` | string | No | Image dimensions (e.g. "1024x1024") |
| `max_images` | integer | No | Batch count (1-15, batch-capable models only) |
| `output_format` | "png" \| "jpeg" | No | Output format |
| `response_format` | "url" \| "b64_json" | No | Response format |
| `watermark` | boolean | No | AIGC watermark |
| `prompt_optimization` | "standard" \| "fast" | No | Prompt optimization mode; 5.0 Pro defaults to `SEEDREAM_PROMPT_OPTIMIZATION_MODE` (`standard`) |
| `persist` | boolean | No | Whether to persist output (default: true) |
| `output_path` | string | No | Local file, or directory ending with `/` (required when `max_images > 1`); stdio only, requires `persist=true`. See [local output paths](#writing-outputs-to-local-paths) |
| `overwrite` | boolean | No | Allow `output_path` to replace an existing file (default: false) |

### Output

Returns a `SeedreamGenerateOutput` with model, created timestamp, artifact
list, per-item errors, and usage info.

A storage failure no longer fails the call. Each image degrades on its own:
URL outputs come back as `id="provider-url"` references to the temporary
provider URL, and `b64_json` outputs as `id="inline-fallback"` references with
the bytes in `fallback_data`, both with `persistence_error` set. Items that were
already stored are unaffected.

## seedream_edit_image

Edit an image through ModelArk Seedream with point or bounding-box targeting.

**Annotations:** `readOnlyHint=False`, `destructiveHint=False`,
`idempotentHint=False`, `openWorldHint=True`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `prompt` | string | Yes | Natural-language edit instruction |
| `images` | list[MediaSource] | Yes | Reference images to edit |
| `point` | EditCoordinate | No* | Point coordinate in normalized `0..999` space |
| `bbox` | EditBbox | No* | Bounding box in normalized `0..999` space |
| `model` | string | No | Override configured model ID |
| `size` | string | No | Image dimensions |
| `output_format` | "png" \| "jpeg" | No | Output format |
| `response_format` | "url" \| "b64_json" | No | Response format |
| `watermark` | boolean | No | AIGC watermark |
| `prompt_optimization` | "standard" \| "fast" | No | Prompt optimization mode; 5.0 Pro defaults to `SEEDREAM_PROMPT_OPTIMIZATION_MODE` (`standard`) |
| `persist` | boolean | No | Whether to persist output (default: true) |
| `output_path` | string | No | Local file, or directory ending with `/`; stdio only, requires `persist=true`. Extra images written to a file path get `-2`, `-3` suffixes |
| `overwrite` | boolean | No | Allow `output_path` to replace an existing file (default: false) |

\* Provide either `point` or `bbox`.

### Output

Returns `SeedreamEditOutput` with artifact list and usage information.
Persistence failures degrade per image exactly as in `seedream_generate_image`.

## seedance_create_task

Create an asynchronous Seedance video generation task.

**Annotations:** `readOnlyHint=False`, `destructiveHint=False`,
`idempotentHint=False`, `openWorldHint=True`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `prompt` | string | No | Text prompt (1-32,000 chars) |
| `images` | list[SeedanceImageInput] | No | Image inputs with roles. Each entry may be a plain URL string or `{"url": ...}` (coerced to `role=reference_image`) |
| `videos` | list[SeedanceVideoInput] | No | Reference videos (max 3). Each entry may be a plain URL string or `{"url": ...}` |
| `audios` | list[SeedanceAudioInput] | No | Reference audio (max 3). Each entry may be a plain URL string or `{"url": ...}` |
| `model` | string | No | Override configured model ID |
| `resolution` | "480p" \| "720p" \| "1080p" \| "4k" | No | Output resolution |
| `ratio` | string | No | Aspect ratio. For `extend_video`, stripped (auto-locks to source) to prevent `InvalidParameter.TaskTypeConstraint`. For `edit_video`, auto-derived from input video. For first/last-frame, locks to first image. |
| `duration` | integer | No | Duration in seconds (-1 for auto, 4-15). Ignored for edit tasks (auto-derived) |
| `omni_reference_task_type` | string | No | Task type hint (e.g. `edit_video`, `extend_video`). Default: `auto` |
| `generate_audio` | boolean | No | Generate audio for the video |
| `watermark` | boolean | No | AIGC watermark |
| `return_last_frame` | boolean | No | Return last frame as image |
| `execution_expires_after` | integer | No | Task TTL in seconds (3600-259200) |
| `priority` | integer | No | Priority (0-9) |
| `safety_identifier` | string | No | Safety identifier (max 64 chars) |

Text-only input (prompt with no media) is supported for pure text-to-video
generation. Audio cannot be the sole media input — at least a prompt,
image, or video is required.

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

### Output

Returns a `SeedanceCreateTaskOutput` with task ID, status, and recommended
poll delay in `recommended_poll_after_ms`.

## seedance_2_5_create_task

Create an asynchronous Seedance 2.5 video generation task. Supports up to
30-second video generation, 50 multimodal references (30 images, 10 videos,
10 audio), and 480p/720p/1080p resolution. Poll with `seedance_get_task`.

**Annotations:** `readOnlyHint=False`, `destructiveHint=False`,
`idempotentHint=False`, `openWorldHint=True`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `prompt` | string | No | Text prompt (1-32,000 chars) |
| `images` | list[SeedanceImageInput] | No | Reference images (max 30; roles `first_frame`, `last_frame`, `reference_image`). Each entry may be a plain URL string or `{"url": ...}` |
| `videos` | list[SeedanceVideoInput] | No | Reference videos (max 10; URL-only). Each entry may be a plain URL string or `{"url": ...}` |
| `audios` | list[SeedanceAudioInput] | No | Reference audio (max 10; audio-only input is supported). Each entry may be a plain URL string or `{"url": ...}` |
| `model` | string | No | Model ID (defaults to `dreamina-seedance-2-5-260628`) |
| `resolution` | "480p" \| "720p" \| "1080p" | No | Output resolution (4k not supported) |
| `ratio` | string | No | Aspect ratio. Stripped for `extend_video`; auto-derived for `edit_video`/first-frame |
| `duration` | integer | No | Duration in seconds (-1 for auto, max 30). Ignored for edit tasks |
| `omni_reference_task_type` | string | No | Task type hint (e.g. `edit_video`, `extend_video`). Default: `auto` |
| `generate_audio` | boolean | No | Generate an audio track for the video |
| `watermark` | boolean | No | AIGC watermark (default: false) |
| `return_last_frame` | boolean | No | Return last frame as a separate image |
| `execution_expires_after` | integer | No | Task TTL in seconds (3600-259200) |
| `priority` | integer | No | Priority (0-9) |
| `safety_identifier` | string | No | Safety identifier (max 64 chars) |

Seedance 2.5 supports audio-only input (a single BGM, voice, or sound-effect
track can drive visual pacing, beat matching, and lip-sync). The model must
resolve to the `seedance_2_5` family or the call raises a `ValueError`.

### Output

Returns a `Seedance25CreateTaskOutput` with task ID, status, and recommended
poll delay in `recommended_poll_after_ms`.

## seedance_get_task

Retrieve the status and output of a Seedance task.

**Annotations:** `readOnlyHint=False`, `destructiveHint=False`,
`idempotentHint=True`, `openWorldHint=False`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `task_id` | string | Yes | Provider task ID from the background result of a Seedance create tool |
| `persist_output` | boolean | No | Persist video/last-frame on success (default: true; background execution required when true) |
| `output_path` | string | No | Local file (or directory ending with `/`) for the video; the last frame is written beside it with a `-last-frame` suffix. stdio only, requires `persist_output=true`. See [local output paths](#writing-outputs-to-local-paths) |
| `overwrite` | boolean | No | Allow `output_path` to replace an existing file (default: false) |

### Output

Returns a `SeedanceTaskOutput` with status, error (if any), video/last-frame
artifact references (on success), usage, generation settings, and `queue`.

`queue` (`SeedanceQueueInfo`) is set only while the task is `queued` or
`running` and is `None` once it finishes. It is derived by this server from the
task's own timestamps, because ModelArk publishes no queue position or ETA:

| Field | Description |
|---|---|
| `queued_seconds` | Seconds queued: now minus `created_at` while queued, or `updated_at` minus `created_at` once running (approximate) |
| `running_seconds` | Seconds since the last status update while running (approximate) |
| `service_tier` | `default` or `flex`; flex tasks usually queue longer |
| `expires_at` | ISO-8601 time after which the provider fails an unfinished task: `created_at + execution_expires_after` |
| `expires_at_estimated` | `true` when the provider did not report `execution_expires_after` and the 48-hour default was assumed |
| `hint` | Human-readable summary to relay to the user |

If the video or last frame cannot be stored, the reference comes back with
`id="provider-url"` and a `persistence_error`; re-persist it with
`seed_media_persist_url` before the 24-hour provider URL expires.

## seedance_get_tasks

Check the status of several Seedance tasks in one call instead of a round of
`seedance_get_task` calls. The server makes one provider list call filtered by
the requested task IDs and re-fetches individually only the tasks missing from
the list or reported succeeded without an output URL. Persistence reuses the
per-task single-flight cache, so overlapping polls never download the same
video twice. Uses the `seedance:read` JWT scope.

**Execution:** Optional background job; background execution is required when
`persist_output=true`.

**Annotations:** `readOnlyHint=False`, `destructiveHint=False`,
`idempotentHint=True`, `openWorldHint=False`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `task_ids` | list[string] | Yes | 1-50 Seedance task IDs; duplicates are ignored |
| `persist_output` | boolean | No | Copy each succeeded task's video and last frame into durable storage, once per task (default: **false**) |
| `output_dir` | string | No | Local directory for each succeeded task's files (`<artifact_id>.<ext>`); stdio only, requires `persist_output=true` |
| `overwrite` | boolean | No | Allow `output_dir` writes to replace existing files (default: false) |

### Output

Returns `SeedanceGetTasksOutput`:

| Field | Description |
|---|---|
| `tasks` | Full status for each found task, in request order, in the same shape as `seedance_get_task` (including `queue`) |
| `errors` | Task IDs that could not be checked, each with `task_id`, `code` (`NOT_OWNED`, `NOT_FOUND`, or a provider code), and `message` |
| `counts` | Number of found tasks per status |
| `all_terminal` | `true` when every found task has finished, so polling can stop |
| `active_tasks` | Number of found tasks still queued or running |

Task IDs not created by the caller are reported in `errors` instead of failing
the batch. A failure of the provider list call itself fails the whole call.

## seedance_list_tasks

List recent Seedance video generation tasks (last 7 days).

**Annotations:** `readOnlyHint=True`, `destructiveHint=False`,
`idempotentHint=True`, `openWorldHint=False`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `page` | integer | No | Page number (1-500) |
| `page_size` | integer | No | Page size (1-100, server caps at 100) |
| `status` | SeedanceTaskStatus | No | Filter by status |
| `task_ids` | list[string] | No | Filter by task IDs |
| `model` | string | No | Filter by model |
| `service_tier` | "default" \| "flex" | No | Filter by service tier |

### Output

Returns a `SeedanceTaskPage` with task summaries, total count, and
pagination info. Each summary includes the same server-derived `queue` timing
as `seedance_get_task` for queued and running tasks.

## seedance_cancel_or_delete_task

Cancel (queued) or delete (terminal) a Seedance task.

**Annotations:** `readOnlyHint=False`, `destructiveHint=True`,
`idempotentHint=False`, `openWorldHint=True`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `task_id` | string | Yes | Task ID |
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


## Parallel Generation Tools

The server also provides parallel generation tools that generate multiple
variations in a single call. Each variation runs independently — partial
failures are captured per variation.

Variations have no individual wall-clock timeout, and time spent waiting for a
concurrency slot never counts against a variation. One batch deadline bounds
the whole call (see [Multi-Generation](multi-generation.md#deadlines-and-failure-phases)).
A variation stopped by the deadline or by a storage failure reports
`error.phase`: `queued` (never started, code `QUEUE_TIMEOUT`, safe to retry),
`generating` (provider call in flight, may have completed), or `persisting`
(output generated, storing it failed). Ordinary provider errors leave `phase`
null. Outputs that could not be stored but have a provider URL or fit inline
are returned as successful variations with `persistence_error` set.

### seedream_generate_image_variations

Generate N independent image variations in parallel with distinct seeds.

**Input:**

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `prompt` | string | No* | — | Base prompt for all variations |
| `variations` | integer | No | 1 | Number of variations (1-10) |
| `variation_prompts` | list[string] | No | — | Explicit prompts per variation |
| `base_seed` | integer | No | — | Base seed. None=random. -1=client-random. N=deterministic (N+i) |
| `images` | list[MediaSource] | No | — | Reference images |
| `model` | string | No | — | Override configured model |
| `size` | string | No | — | Image dimensions |
| `output_format` | "png" \| "jpeg" | No | — | Output format |
| `response_format` | "url" \| "b64_json" | No | — | Response format |
| `watermark` | boolean | No | — | AIGC watermark |
| `prompt_optimization` | "standard" \| "fast" | No | `SEEDREAM_PROMPT_OPTIMIZATION_MODE` on 5.0 Pro | Optimization mode |
| `persist` | boolean | No | true | Persist to artifact store |
| `output_dir` | string | No | — | Local directory for each variation image; stdio only, requires `persist=true` |
| `overwrite` | boolean | No | false | Allow `output_dir` writes to replace existing files |

\* Either `prompt` or `variation_prompts` must be provided.

**Output:** `VariationSummary` with `total`, `succeeded`, `failed`, and
per-variation results (artifact or error).

### seed_audio_generate_variations

Generate N independent audio variations in parallel.

**Input:**

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `text_prompt` | string | No* | — | Base prompt (1-3000 chars) |
| `variations` | integer | No | 1 | Number of variations (1-5) |
| `variation_prompts` | list[string] | No | — | Explicit prompts per variation |
| `audio_references` | list[AudioReference] | No | — | Up to 3 audio references (Base64 WAV preflight-checked against 30s limit) |
| `image_reference` | MediaSource | No | — | Image reference (mutually exclusive with audio) |
| `output` | AudioOutputOptions | No | — | Format, sample rate, etc. |
| `watermark` | AudioWatermarkOptions | No | — | AIGC watermark |
| `persist` | boolean | No | true | Persist to artifact store |
| `output_dir` | string | No | — | Local directory for each variation audio; stdio only, requires `persist=true` |
| `overwrite` | boolean | No | false | Allow `output_dir` writes to replace existing files |

\* Either `text_prompt` or `variation_prompts` must be provided.

### seedance_create_task_variations

Create N independent Seedance video tasks in parallel. The background result
contains the per-variation provider task IDs for async polling via
`seedance_get_task`.

**Input:** Inherits all fields from `seedance_create_task`, plus:

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `variations` | integer | No | 1 | Number of variations (1-5) |
| `variation_prompts` | list[string] | No | — | Explicit prompts per variation (each 1-32,000 chars) |

\* Either `prompt` or `variation_prompts` must be provided.

**Output:** `VariationSummary` + `recommended_poll_after_ms`.

### seedance_2_5_create_task_variations

Create N independent Seedance 2.5 video tasks in parallel (each a separate
provider task). Retrieve the background result, then pass each provider task ID
to `seedance_get_task`; partial failures are captured per variation.

**Annotations:** `readOnlyHint=False`, `destructiveHint=False`,
`idempotentHint=False`, `openWorldHint=True`

**Input:** Inherits all fields from `seedance_2_5_create_task`, plus:

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `variations` | integer | No | 1 | Number of variations (1-5) |
| `variation_prompts` | list[string] | No | — | Explicit prompts per variation (each 1-32,000 chars) |

\* Either `prompt` or `variation_prompts` must be provided.

**Output:** `VariationSummary` + `recommended_poll_after_ms`.

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

Upload image, audio, or video media to object storage (TOS or S3) and receive
a presigned HTTPS URL.

**Execution:** Required background job because a video upload can contain up to
200 MiB.

**Annotations:** `readOnlyHint=False`, `destructiveHint=False`,
`idempotentHint=False`, `openWorldHint=True`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `media_type` | "image" \| "audio" \| "video" | Yes | Media category for validation |
| `mime_type` | string | Yes | MIME type such as `video/mp4` or `image/png` |
| `data` | string | No* | Base64-encoded media bytes |
| `file_path` | string | No* | Absolute local path; intended for stdio transport |
| `key_prefix` | string | No | Optional object key prefix |
| `expires_in_seconds` | integer | No | Presigned URL validity (60–604800). Defaults to the configured TTL; use a long value (e.g. 3600) for VOD inputs that are fetched asynchronously |

\* Provide exactly one of `data` or `file_path`.

### Output

Returns `MediaUploadOutput` with presigned `url`, `expires_at`, `object_key`,
and uploaded `bytes`. For several files, use `media_upload_batch`.

## media_upload_batch

Upload 1-50 media files in one call and receive a presigned HTTPS URL for each,
instead of N sequential `media_upload` calls. Each item follows the same rules
as `media_upload` and fails independently. Registered with object storage and
uses the `media:upload` JWT scope.

**Execution:** Required background job (use `ark_job_submit`), because batches
can be large.

**Annotations:** `readOnlyHint=False`, `destructiveHint=False`,
`idempotentHint=False`, `openWorldHint=True`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `items` | list[MediaUploadBatchItemInput] | Yes | 1-50 files to upload |
| `expires_in_seconds` | integer | No | Presigned URL validity (60–604800) applied to every item. Defaults to the configured TTL |
| `max_concurrent` | integer | No | Uploads running at the same time (1-8, default 4) |

**MediaUploadBatchItemInput:**

| Field | Type | Required | Description |
|---|---|---|---|
| `media_type` | "image" \| "audio" \| "video" | Yes | Media category for validation |
| `mime_type` | string | No* | MIME type; optional for `file_path` items (inferred from the file extension), required for `data` items |
| `data` | string | No** | Base64-encoded media bytes |
| `file_path` | string | No** | Absolute local path; stdio transport only |
| `key_prefix` | string | No | Optional object key prefix (default `references`) |

\* When the MIME type cannot be inferred from the extension, the request
fails input validation and asks for `mime_type`. \*\* Provide exactly one of `data` or `file_path` per item.

Every item is validated, and the total decoded size is checked against
`MEDIA_UPLOAD_BATCH_MAX_BYTES` (default 500 MiB), before any upload starts. An
oversized batch fails as a whole; split it into smaller batches.

### Output

Returns `MediaUploadBatchOutput` with `items` in request order (each with
`index`, `file_path`, `url`, `object_key`, `expires_at`, `mime_type`, `bytes`,
`error`, and `retryable`), plus `succeeded`, `failed`, and `total_bytes`.

## media_presign

Generate a fresh presigned HTTPS GET URL for an existing object in storage
without re-uploading.  Use this when a previously uploaded reference's
presigned URL has expired or is about to expire.

**Annotations:** `readOnlyHint=True`, `destructiveHint=False`,
`idempotentHint=True`, `openWorldHint=False`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `object_key` | string | Yes | Object key returned by a prior `media_upload` call |
| `expires_in_seconds` | integer | No | Presigned URL validity (60–604800). Defaults to the configured TTL; use a long value (e.g. 3600) for VOD inputs that are fetched asynchronously |

### Output

Returns `MediaPresignOutput` with presigned `url`, `expires_at`, and
`object_key`.

## media_presign_batch

Generate fresh presigned HTTPS GET URLs for many existing objects in storage
in a single call, without re-uploading. Accepts a list of object keys from
prior `media_upload` calls. Failures are reported per key — a malformed,
unowned, or provider-failing key returns an inline error while the rest
succeed.

**Annotations:** `readOnlyHint=True`, `destructiveHint=False`,
`idempotentHint=True`, `openWorldHint=False`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `object_keys` | list[string] | Yes | Object keys returned by prior `media_upload` calls (1–100 entries) |
| `expires_in_seconds` | integer | No | Presigned URL validity (60–604800) applied to every key. Defaults to the configured TTL |

### Output

Returns `MediaPresignBatchOutput` with `items` (per-key `object_key`, `url`,
`expires_at`, `code`, `error`, `request_id`), `succeeded`, and `failed`.

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

## hyper3d_get_task / hitem3d_get_task

Retrieve a 3D task's status and, on first success, persist the provider's
zip URL into durable artifact storage (`seed-media://artifacts/{id}`).

**Execution:** Optional background job. Poll in foreground with
`persist_output=false`, then use background execution with `persist_output=true`
for the completed download.

With `persist_output=true`, `output_path` (a file, or a directory ending with
`/`) and `overwrite` also write the 3D file to a
[local path](#writing-outputs-to-local-paths). If the file cannot be stored,
the reference comes back with `id="provider-url"` and a `persistence_error`;
re-persist it with `seed_media_persist_url` before the provider URL expires.

## hyper3d_list_tasks / hitem3d_list_tasks

List recent 3D tasks (provider keeps 7 days), filtered by status, task IDs,
or model.

## hyper3d_cancel_or_delete_task / hitem3d_cancel_or_delete_task

Cancel a queued task or delete the record of a terminal (succeeded, failed,
expired) task. The handler verifies `expected_status` matches before acting.
