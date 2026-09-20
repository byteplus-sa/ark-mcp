# Tools Reference

The server exposes a conditional set of typed tools. `seed_media_get_artifact`
and `seed_media_export_artifact` are always available, provider tools are
registered only when their credentials
are configured, and `media_upload`, `media_presign`, and `media_presign_batch`
are registered when object storage credentials (TOS or S3) are present. Each
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
| `destination_path` | string | No | Absolute path where the server writes an atomic copy; omit to return the canonical store path |

### Output

Returns `SeedMediaExportArtifactOutput` with `artifact_id`, `path`,
`media_type`, `mime_type`, `bytes`, `sha256`, and `copied`. `copied` is `false`
when `path` is the canonical store location (filesystem backend) and `true`
when the artifact was copied to `destination_path`.

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

**Annotations:** `readOnlyHint=True`, `destructiveHint=False`,
`idempotentHint=True`, `openWorldHint=False`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `task_id` | string | Yes | Provider task ID from the background result of `vod_enhance_video` |
| `persist_output` | boolean | No | Persist the completed output on first successful poll (default: true; background execution required when true) |

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

**Annotations:** `readOnlyHint=True`, `destructiveHint=False`,
`idempotentHint=True`, `openWorldHint=False`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `task_id` | string | Yes | Provider task ID from the background result of `vod_transcode_video` |
| `persist_output` | boolean | No | Persist the completed output on first successful poll (default: true; background execution required when true) |

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
carry the safe provider failure detail. GET polling is retried only on
provider-marked retryable errors (e.g. 429).

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
use `persist_output=false` for a foreground status check.

**Annotations:** `readOnlyHint=True`, `destructiveHint=False`,
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
`vod_get_subtitle_addition_task`. The tool uses `vod:read`; persistence requires
background execution, while `persist_output=false` allows a foreground
status check.

**Annotations:** `readOnlyHint=True`, `destructiveHint=False`,
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

**Annotations:** `readOnlyHint=True`, `destructiveHint=False`,
`idempotentHint=True`, `openWorldHint=False`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `task_id` | string | Yes | Provider task ID from the background result of `vod_separate_audio` |
| `persist_output` | boolean | No | Copy completed tracks into durable artifact storage on first successful poll (default `true`; background execution required when true) |

### Output and execution limits

Returns `VodAudioSeparationTaskOutput` with a normalized `status` of
`processing`, `succeeded`, or `failed`. On success, `voice`, `background`,
`music`, and `sfx` each carry the track's expiring `source_url` (valid 24
hours) and, when best-effort persistence succeeds, a durable `artifact`
reference. `persist_output=true` copies each track once and caches it by task
ID so repeated polls do not re-download; persistence is reported per track as
`not_requested`, `persisted`, or `failed`, and a failed copy does not erase
provider success. On failure, `error.code`/`error.message` carry the safe
provider detail. GET polling is retried only on provider-marked retryable
errors (e.g. 429).

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

### Output

Returns a `SeedAudioGenerateOutput` with `duration_seconds`,
`billing_duration_seconds`, `artifact`, optional `subtitle`, `request_id`,
`provider_log_id`, and optional `source_url`.

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
| `prompt_optimization` | "standard" \| "fast" | No | Prompt optimization mode; defaults to standard on 5.0 |
| `persist` | boolean | No | Whether to persist output (default: true) |

### Output

Returns a `SeedreamGenerateOutput` with model, created timestamp, artifact
list, per-item errors, and usage info.

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
| `prompt_optimization` | "standard" \| "fast" | No | Prompt optimization mode; defaults to standard on 5.0 |
| `persist` | boolean | No | Whether to persist output (default: true) |

\* Provide either `point` or `bbox`.

### Output

Returns `SeedreamEditOutput` with artifact list and usage information.

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

**Annotations:** `readOnlyHint=True`, `destructiveHint=False`,
`idempotentHint=True`, `openWorldHint=False`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `task_id` | string | Yes | Provider task ID from the background result of a Seedance create tool |
| `persist_output` | boolean | No | Persist video/last-frame on success (default: true; background execution required when true) |

### Output

Returns a `SeedanceTaskOutput` with status, error (if any), video/last-frame
artifact references (on success), usage, and generation settings.

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
pagination info.

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

The server also provides three parallel generation tools that generate
multiple variations in a single call using `asyncio.gather`. Each variation
runs independently — partial failures are captured per variation.

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
| `prompt_optimization` | "standard" \| "fast" | No | standard on 5.0 | Optimization mode |
| `persist` | boolean | No | true | Persist to artifact store |

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
multimodal model. Supports deep-thinking (chain-of-thought) reasoning when
`thinking=true` (deep-thinking is opt-in, not default-on). The default model
is `dola-seed-2-1-turbo-260628` (Seed 2.1 Turbo); `dola-seed-evolving` (the
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
duration.

**Annotations:** `readOnlyHint=True`, `destructiveHint=False`,
`idempotentHint=False`, `openWorldHint=True`

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `prompt` | string | Yes | The question or task for the model to reason about (1-32,000 chars) |
| `images` | list[UnderstandingImageInput] | No | Images to understand (URL or Base64, max 32) |
| `videos` | list[UnderstandingVideoInput] | No | Videos to understand (URL only, max 32) |
| `system` | string | No | Optional system instruction (max 32,000 chars) |
| `model` | string | No | Override the configured Seed 2.1 model ID |
| `thinking` | boolean | No | Enable deep-thinking reasoning (default: false) |
| `reasoning_effort` | "low" \| "medium" \| "high" | No | Reasoning effort level (only when thinking=true) |
| `temperature` | float | No | Sampling temperature (0.0-2.0) |
| `max_tokens` | integer | No | Maximum output tokens (1-32,768) |
| `top_p` | float | No | Nucleus sampling probability (0.0-1.0) |
| `repetition_penalty` | float | No | Repetition penalty (0.0-2.0). Ark-only parameter. |

**UnderstandingImageInput** and **UnderstandingVideoInput** are `MediaSource`
subclasses with the appropriate MIME validation and size limits. Video Base64
is not supported by the chat endpoint — upload local videos via `media_upload`
first to get an HTTPS URL.

### Output

Returns `SeedUnderstandOutput` with `model`, `completion_id`, `choices`
(each containing `content`, optional `reasoning_content`, and
`finish_reason`), `usage` (token counts), and `request_id`.

### Example

```json
{
  "prompt": "Describe what happens in this video and identify the objects in this image.",
  "videos": [{"kind": "url", "url": "https://.../sample.mp4", "mime_type": "video/mp4"}],
  "images": [{"kind": "url", "url": "https://.../frame.png", "mime_type": "image/png"}],
  "thinking": true,
  "reasoning_effort": "medium",
  "max_tokens": 4096
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
and uploaded `bytes`.

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

## hyper3d_list_tasks / hitem3d_list_tasks

List recent 3D tasks (provider keeps 7 days), filtered by status, task IDs,
or model.

## hyper3d_cancel_or_delete_task / hitem3d_cancel_or_delete_task

Cancel a queued task or delete the record of a terminal (succeeded, failed,
expired) task. The handler verifies `expected_status` matches before acting.
