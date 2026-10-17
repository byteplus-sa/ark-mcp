# API Reference

Complete schemas, inputs, outputs, and examples for the conditional MCP tool
surface.

## Tool Inventory

| # | Tool | Product | Type | Auth |
|---|---|---|---|---|
| 0 | `ark_list_models` | Discovery | Read-only | None |
| 1 | `seed_audio_generate` | Seed Audio | Required background task | Seed Speech |
| 2 | `seed_audio_generate_variations` | Seed Audio | Required background task | Seed Speech |
| 3 | `seedream_generate_image` | Seedream | Required background task | ModelArk |
| 4 | `seedream_generate_image_variations` | Seedream | Required background task | ModelArk |
| 5 | `seedance_create_task` | Seedance (every configured model, incl. 2.5 Premium when enabled) | Required background submission | ModelArk |
| 7 | `gen_task_get` | ModelArk generation tasks | Optional background retrieval | ModelArk |
| 8 | `gen_task_list` | ModelArk generation tasks | Read-only | ModelArk |
| 9 | `gen_task_cancel_or_delete` | ModelArk generation tasks | Destructive | ModelArk |
| 10 | `media_upload` | Object storage (optional) | Required background task | TOS / S3 |
| 11 | `media_presign` | Object storage (optional) | Read-only | TOS / S3 |
| 12 | `media_presign_batch` | Object storage (optional) | Read-only | TOS / S3 |
| 13 | `seedream_edit_image` | Seedream | Required background task | ModelArk |
| 14 | `seed_understand` | Seed 2.1 (optional) | Required background task | ModelArk |
| 17 | `seed_media_get_artifact` | Artifacts | Read-only | Local / JWT |
| 18 | `speech_to_text` | Seed Speech ASR (optional) | Required background task | Seed Speech |
| 19 | `vod_enhance_video` | VOD AI MediaKit (optional) | Required background submission | MediaKit Bearer |
| 20 | `vod_transcode_video` | VOD AI MediaKit (optional) | Required background submission | MediaKit Bearer |
| 21 | `vod_separate_audio` | VOD AI MediaKit (optional) | Required background submission | MediaKit Bearer |
| 22 | `vod_add_subtitles` | VOD AI MediaKit (optional) | Required background submission | MediaKit Bearer |
| 23 | `vod_remove_subtitles` | VOD AI MediaKit (optional) | Required background submission | MediaKit Bearer |
| 24 | `vod_get_task` | VOD AI MediaKit (optional) | Optional background retrieval | MediaKit Bearer |
| 25 | `hyper3d_create_task` | Hyper3D (optional) | Required background submission | ModelArk |
| 26 | `hitem3d_create_task` | Hitem3d (optional) | Required background submission | ModelArk |
| 27 | `ark_job_capabilities` | Background jobs | Read-only ordinary tool | Local / JWT |
| 28 | `ark_job_submit` | Background jobs | Default background submission path | Dynamic target scope |
| 29 | `ark_job_get` | Background jobs | Read-only ordinary tool | Owner only |
| 30 | `ark_job_cancel` | Background jobs | Destructive ordinary tool | Owner only |
| 31 | `seed_media_export_artifact` | Artifacts | Locate (read-only) or copy (stdio only) | Local / JWT |
| 32 | `seed_media_persist_url` | Artifacts | Optional background task | Local / JWT |
| 33 | `media_upload_batch` | Object storage (optional) | Required background task | TOS / S3 |
| 34 | `seed_audio_understand` | Seed audio understanding (optional) | Required background task | ModelArk |
| 35 | `ark_asset_group_ensure` | Asset library (optional) | Write | BytePlus AK/SK |
| 36 | `ark_asset_group_create` | Asset library (optional) | Write | BytePlus AK/SK |
| 37 | `ark_asset_group_get` | Asset library (optional) | Read-only | BytePlus AK/SK |
| 38 | `ark_asset_group_list` | Asset library (optional) | Read-only | BytePlus AK/SK |
| 39 | `ark_asset_group_update` | Asset library (optional) | Write | BytePlus AK/SK |
| 40 | `ark_asset_create` | Asset library (optional) | Optional background task | BytePlus AK/SK |
| 41 | `ark_asset_get` | Asset library (optional) | Read-only | BytePlus AK/SK |
| 42 | `ark_asset_list` | Asset library (optional) | Read-only | BytePlus AK/SK |
| 43 | `ark_asset_update` | Asset library (optional) | Write | BytePlus AK/SK |
| 44 | `ark_asset_verification_start` | Asset library (optional) | Write | BytePlus AK/SK |
| 45 | `ark_asset_verification_result` | Asset library (optional) | Optional background retrieval | BytePlus AK/SK |
| 46 | `ark_asset_delete` | Asset library (optional) | Destructive (opt-in) | BytePlus AK/SK |
| 47 | `ark_asset_group_delete` | Asset library (optional) | Destructive (opt-in) | BytePlus AK/SK |
| 50 | `omnihuman_create_task` | OmniHuman 2.0 (flag-gated) | Required background submission | Vision AI Bearer |
| 51 | `omnihuman_get_task` | OmniHuman 2.0 (flag-gated) | Optional background retrieval | Vision AI Bearer |
| 52 | `omnihuman_cancel_task` | OmniHuman 2.0 (flag-gated) | Destructive | Vision AI Bearer |

Numbers are stable section identifiers; **gaps (6, 15, 16, 48, 49) mark tools
merged into `seedance_create_task`**. That one tool serves every configured
Seedance model (pick one from `ark_list_models(kind='video')`), is registered
whenever `BYTEPLUS_MODELARK_API_KEY` is set, and requires `seedance:create` in
JWT mode. Seedance 2.5 Premium has no separate tool: its model ID is bound only
when `BYTEPLUS_MODELARK_SEEDANCE_2_5_PREMIUM_ENABLED=true`.

The three `gen_task_*` tools poll, list, and cancel or delete **every ModelArk
generation task** (Seedance, Hyper3D, and Hitem3d). They are registered whenever
ModelArk is configured, independent of `BYTEPLUS_MODELARK_3D_ENABLED`, and
require `gen:read` (`gen_task_get`, `gen_task_list`) or `gen:delete`
(`gen_task_cancel_or_delete`) in JWT mode. `vod_get_task` polls **every VOD AI
MediaKit task**, is registered when `BYTEPLUS_VOD_MEDIAKIT_API_KEY` is set, and
requires `vod:read`. See [security.md](security.md) for the full scope list.

## Tool Annotations

| Tool | readOnly | destructive | idempotent | openWorld |
|---|---|---|---|---|
| `seed_audio_generate` | false | false | false | true |
| `seed_audio_generate_variations` | false | false | false | true |
| `seedream_generate_image` | false | false | false | true |
| `seedream_generate_image_variations` | false | false | false | true |
| `seedance_create_task` | false | false | false | true |
| `gen_task_get` | false | false | true | false |
| `gen_task_list` | true | false | true | false |
| `gen_task_cancel_or_delete` | false | true | false | true |
| `media_upload` | false | false | false | true |
| `media_presign` | true | false | true | false |
| `media_presign_batch` | true | false | true | false |
| `seedream_edit_image` | false | false | false | true |
| `seed_understand` | false | false | false | true |
| `seed_audio_understand` | false | false | false | true |
| `seed_media_get_artifact` | true | false | true | false |
| `seed_media_export_artifact` | false | false | true | false |
| `speech_to_text` | true | false | true | false |
| `vod_enhance_video` | false | false | false | true |
| `vod_transcode_video` | false | false | false | true |
| `vod_separate_audio` | false | false | false | true |
| `vod_add_subtitles` | false | false | false | true |
| `vod_remove_subtitles` | false | false | false | true |
| `vod_get_task` | false | false | true | false |
| `hyper3d_create_task` | false | false | false | true |
| `hitem3d_create_task` | false | false | false | true |
| `omnihuman_create_task` | false | false | false | true |
| `omnihuman_get_task` | false | false | true | true |
| `omnihuman_cancel_task` | false | true | false | true |
| `ark_job_capabilities` | true | false | true | false |
| `ark_job_submit` | false | false | false | true |
| `ark_job_get` | true | false | true | false |
| `ark_job_cancel` | false | true | false | true |
| `seed_media_persist_url` | false | false | false | true |
| `media_upload_batch` | false | false | false | true |
| `ark_asset_group_ensure` | false | false | true | true |
| `ark_asset_group_create` | false | false | false | true |
| `ark_asset_group_get` | true | false | true | true |
| `ark_asset_group_list` | true | false | true | true |
| `ark_asset_group_update` | false | false | true | true |
| `ark_asset_create` | false | false | false | true |
| `ark_asset_get` | true | false | true | true |
| `ark_asset_list` | true | false | true | true |
| `ark_asset_update` | false | false | true | true |
| `ark_asset_verification_start` | false | false | false | true |
| `ark_asset_verification_result` | true | false | true | true |
| `ark_asset_delete` | false | true | false | true |
| `ark_asset_group_delete` | false | true | false | true |

`seed_understand` and `seed_audio_understand` are not read-only because `save_to`
writes local files, and the task pollers (`gen_task_get` and `vod_get_task`) are
not read-only because `output_dir`/`output_path` write local files.

## Background Execution

Generation and variation tools, `speech_to_text`, `media_upload`,
`media_upload_batch`, `seed_understand`, `seed_audio_understand`, and all Seedance, Seed 3D, and
MediaKit provider-submission tools declare `execution.taskSupport="required"`. By default, discover them
with `ark_job_capabilities`, submit them through `ark_job_submit`, and poll
`ark_job_get` at the advertised two-second interval until terminal. A
task-capable client may instead invoke them with MCP task metadata and read the
result from the `tasks/get` response. Foreground direct calls to those target
tools fail before the provider is contacted.

In this reference, **background result** means the original tool result under
terminal `ark_job_get.result`, or the terminal `tasks/get` response on the
native path.

The task pollers `gen_task_get` (Seedance and Seed 3D) and `vod_get_task`
(MediaKit), and `seed_media_persist_url`, declare
`execution.taskSupport="optional"` because a processing-status check is short,
but a successful response with `persist_output=true` may download a large
artifact. Use foreground execution with persistence disabled for quick polling,
then background execution to retrieve and persist completed output. **Note the
defaults differ:** `gen_task_get` defaults to `persist_output=false`, while
`vod_get_task` defaults to `persist_output=true`, so a foreground
`vod_get_task` call must pass `persist_output=false`. List, presign,
artifact-read, and cancel/delete tools remain foreground operations.

The ordinary background-job tools, usable on any MCP client:

1. `ark_job_capabilities()` returns the configured, scope-filtered targets and
   each target's original input schema.
2. `ark_job_submit({"tool_name": name, "arguments": original_arguments})`
   durably enqueues the same registered task-enabled tool and returns a
   server-generated Ark `job_id`.
3. `ark_job_get({"job_id": id})` returns `working` or a terminal snapshot. On
   completion, `result` preserves the original MCP tool result.
4. `ark_job_cancel({"job_id": id})` cooperatively cancels the local job.

The ordinary path uses the same worker, target schema validation, JWT
target scope, tenant/principal ownership, replay claim, budget, concurrency,
metrics, and provider adapter as native task augmentation. Ark job IDs and
provider task IDs are separate identifiers.

## Local Output Paths

On stdio transport, these fields write output straight to a local file:
`output_path` on `seedream_generate_image`, `seedream_edit_image`,
`seed_audio_generate`, and `vod_get_task`; `output_dir` on
`seedream_generate_image_variations`, `seed_audio_generate_variations`, and
`gen_task_get`; `save_to` on `seed_understand` and `seed_audio_understand`;
and `destination_path`
on `seed_media_export_artifact`. Each comes with an `overwrite` boolean
(default `false`).

| Rule | Behavior |
|---|---|
| Transport | stdio only; rejected on HTTP |
| Path form | Absolute. `output_path` names a file, or a directory when it ends with `/` (files named `<artifact_id>.<ext>`) |
| Allowed roots | Client MCP roots (`roots/list`) first, otherwise `OUTPUT_ROOTS`; with neither, path writing is disabled |
| Timing | Validated before any provider call |
| Preconditions | `persist=true` (generation tools) or `persist_output=true` (task pollers); multi-image `seedream_generate_image` (`max_images > 1`) and `vod_get_task` with `operation="separate_audio"` need a directory |
| Existing files | Replaced only with `overwrite=true`; identical content is success |
| Failure | A failed local write never fails the call; see `ArtifactRef.local_path` / `export_error` |

`gen_task_get` writes every persisted output of each succeeded task (video,
last frame, or 3D model file) into `output_dir` as `<artifact_id>.<ext>`.
`vod_get_task` with `separate_audio` writes **every** separated track into the
`output_path` directory.

---

## vod_enhance_video

Enhance a public HTTPS video through the Bearer-authenticated BytePlus VOD AI
MediaKit convenience endpoint. The tool is registered only when
`BYTEPLUS_VOD_MEDIAKIT_API_KEY` is configured and requires the `vod:enhance`
scope in JWT mode.

The tool selects an enhancement tier (`fast`, `standard`, or `professional`) and a
target resolution from 240p up to 8K (`fast` tops out at 4K) or a short-side pixel
limit. `standard` and `professional` submit to `/tools/enhance-video`; `fast` submits
to `/tools/enhance-video-fast`. Defaults preserve the previous profile:
`professional` / `4k` / `high` / 24 fps. Retrieve
the provider task ID and `operation` (`"enhance"`) from the background result,
then poll them with [`vod_get_task`](#vod_get_task). The POST is non-idempotent
and is never retried automatically because a timeout may occur after the provider
began processing.

### Input

| Field | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `video_url` | URL | Yes | — | Public HTTPS source; private, loopback, and link-local targets rejected |
| `tool_version` | `"fast"` \| `"standard"` \| `"professional"` | No | `"professional"` | Enhancement tier; `fast` uses its own endpoint |
| `resolution` | `"240p"` … `"4k"`, `"6k"`, `"8k"` | No | `"4k"` when `resolution_limit` is unset | `fast` accepts up to `"4k"`; mutually exclusive with `resolution_limit` |
| `resolution_limit` | integer | No | — | 128–4320 (128–2160 for `fast`); short-side pixels, aspect ratio preserved |
| `bitrate_level` | `"low"` \| `"medium"` \| `"high"` | No | `"high"` | Ignored when `bitrate` is set |
| `bitrate` | integer | No | — | kbps, 10–150000 |
| `fps` | number \| null | No | `24` | 15–120; `null` keeps the source frame rate |
| `scene` | `"common"` \| `"ugc"` \| `"short_series"` \| `"aigc"` \| `"old_film"` | No | `"common"` | Effective for `standard` only; not sent for `fast` |
| `enhance_style` | `"hd"` \| `"natural"` | No | — | `standard`/`professional` only; rejected for `fast` |
| `project` | string | No | `"default"` | 1–128 characters; serialized upstream as `Project` |
| `input_duration_seconds` | number \| null | No | `null` | Positive; reserved for future pricing support |
| `persist` | boolean | No | `true` | Best-effort durable artifact copy |

### Output

Returns `VodEnhanceVideoOutput` with provider `byteplus-vod-mediakit`,
`status="accepted"`, task/request IDs, and `operation="enhance"`. If the provider directly returns a
completed result, status is `succeeded` and `source_url` is preserved whether
persistence succeeds, is skipped, or fails.

When `persist=true`, the server attempts an SSRF-safe copy into the artifact
store. Video persistence is capped at 200 MiB. `persistence` is one of
`not_applicable`, `persisted`, `failed`, or `not_requested`; `persistence_issue` safely explains
a failure without exposing the URL or credential. `video` contains the durable
`ArtifactRef` only when persistence succeeds. `estimated_cost_usd` is always
`null` until the convenience endpoint's pricing and billing-unit mapping are
confirmed.

The asynchronous acceptance and completed task shapes are verified by sanitized
live probes. Unknown shapes fail closed.

### Example

```json
{
  "video_url": "https://media.example.com/source.mp4",
  "tool_version": "fast",
  "resolution": "1080p",
  "bitrate_level": "medium",
  "fps": null,
  "persist": true
}
```

---

## vod_transcode_video

Submit an asynchronous BytePlus VOD AI MediaKit video transcoding task through
the Bearer-authenticated convenience endpoint. Registered only when
`BYTEPLUS_VOD_MEDIAKIT_API_KEY` is configured; requires the `vod:transcode`
scope in JWT mode.

The request body and `video` object field names and enums are verified from the
official AI MediaKit API reference. The default options reproduce the verified
portrait-to-720x720 letterbox profile. Submission returns `status="accepted"`
with a `task_id` and `operation="transcode"` for polling via
[`vod_get_task`](#vod_get_task). The non-idempotent
POST is never retried automatically because a timeout may occur after the
provider began processing.

### Input

| Field | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `video_url` | URL | Yes | — | Public HTTPS source; private, loopback, and link-local targets rejected |
| `container_format` | `"MP4"` \| `"FLV"` \| `"MPEGTS"` | No | `"MP4"` | Output container format |
| `video` | VodTranscodeVideoOptions | No | default profile | See below |

**VodTranscodeVideoOptions:**

| Field | Type | Default | Constraints |
|---|---|---|---|
| `codec` | `"h264"` \| `"h265"` | `"h264"` | Output video codec |
| `scale_type` | `0` \| `1` \| `2` | `2` | `0` follow source, `1` long/short-side limit, `2` width/height limit |
| `scale_mode` | `0` \| `1` \| `2` | `2` | `0` no upsampling, `1` stretch, `2` letterbox with black bars |
| `scale_width` | integer \| null | `null` | px [0,4320]; only when `scale_type=2`; defaults to 720 |
| `scale_height` | integer \| null | `null` | px [0,4320]; only when `scale_type=2`; defaults to 720 |
| `scale_short` | integer \| null | `null` | px [0,4320]; only when `scale_type=1` |
| `scale_long` | integer \| null | `null` | px [0,4320]; only when `scale_type=1` |
| `bitrate_mode` | `"crf"` \| `"abr"` \| `"cbr"` | `"crf"` | Bitrate control mode |
| `bitrate_crf` | integer | `25` | [0,51]; only used when `bitrate_mode=crf` |
| `bitrate_kbps` | integer | `2000` | kbps [10,50000] |
| `fps_mode` | `"vfr"` \| `"cfr"` | `"vfr"` | Only takes effect after `fps` is set |
| `fps` | integer \| null | `null` | [1,240]; unset keeps source rate |
| `is_hdr_to_sdr` | boolean | `true` | Convert HDR to SDR; false keeps HDR |

### Output

Returns `VodTranscodeVideoOutput` with `provider` `byteplus-vod-mediakit`,
`status="accepted"`, task/request IDs, `operation="transcode"`, and a
server-side heuristic `recommended_poll_after_ms`.

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

---

## vod_add_subtitles

Submit `POST /tools/add-subtitle-to-video` using the configured MediaKit Bearer
key and the `vod:subtitle:add` JWT scope. `video_url` is required and must be a
public HTTPS URL. Provide at least one of `subtitle_url` (SRT, VTT, or ASS) or
`subtitles`, where every inline cue has nonblank `subtitle_text` and an
`end_time` greater than its nonnegative `start_time`. A subtitle file takes
priority when both forms are present.

Optional style fields control the position preset, positive pixel font size,
RGBA color, and MediaKit font. `client_token` is at most 64 printable ASCII
characters; callback payloads are capped at 512 UTF-8 bytes. `callback_url`
must be public HTTPS. `project` is a legacy case-sensitive `Project` extension
and is omitted by default. The accepted output includes `task_id`,
`operation="add_subtitles"`, request IDs, and a 5-second initial polling
heuristic. Poll with [`vod_get_task`](#vod_get_task).


## vod_remove_subtitles

Submit `POST /tools/erase-video-subtitle-pro` using `vod:subtitle:remove`.
`mode="subtitle"` removes recognized dialogue subtitles; the broader
`mode="text"` may also remove titles, labels, or watermarks. Encoding mode is
`quality` or `size`. Optional precision controls include one-to-twenty
normalized erasure rectangles, selected/skipped time segments, and subtitle
OCR size/centering thresholds. Callback, queue, and `client_token` fields match
subtitle addition. The legacy `model_version` (`v4`/`v5`) and `project` fields
are omitted unless explicitly supplied. The output includes the task ID,
`operation="remove_subtitles"`, and a 15-second initial polling heuristic. Poll
with [`vod_get_task`](#vod_get_task).


## vod_separate_audio

Submit an asynchronous BytePlus VOD AI MediaKit voice and background audio
separation task via `POST /api/v1/tools/separate-voice`. The tool is registered
when `BYTEPLUS_VOD_MEDIAKIT_API_KEY` is configured and requires the
`vod:extract` scope in JWT mode.

Input takes a public HTTPS source URL and separation options:

| Field | Type | Required | Description |
|---|---|---|---|
| `audio_url` | string | No | Public HTTPS audio URL (mp3, m4a, wav). Exactly one of `audio_url`/`video_url` |
| `video_url` | string | No | Public HTTPS video URL (mp4, flv, ts, avi, mov, wmv, mkv). Exactly one of `audio_url`/`video_url` |
| `scene` | string | No | `Audio` (default), `Music`, `Drama`, `Narrate` |
| `output_format` | string | No | `aac` (default), `mp3`, `wav`, `m4a`, `flac` |

Returns `VodSeparateAudioOutput` with `provider` `byteplus-vod-mediakit`,
`status` `accepted`, `operation="separate_audio"`, the provider `request_id` and
`provider_log_id`. Run it in the background, then pass the provider task ID and
`operation` from its result to [`vod_get_task`](#vod_get_task). The mutation is never retried
automatically because completion is ambiguous after a timeout.

### Example

```json
// Input
{ "video_url": "https://example.com/clip.mp4", "scene": "Drama" }

// Output
{
  "provider": "byteplus-vod-mediakit",
  "status": "accepted",
  "request_id": "20260820...",
  "provider_log_id": "20260820...",
  "task_id": "amk-tool-separate-voice-...",
  "operation": "separate_audio",
  "recommended_poll_after_ms": 3000
}
```

---

## vod_get_task

**One poller for every VOD AI MediaKit task.** Pass the `task_id` and
`operation` that any `vod_*` create tool returned (`vod_enhance_video`,
`vod_transcode_video`, `vod_separate_audio`, `vod_add_subtitles`, or
`vod_remove_subtitles`). Registered only when `BYTEPLUS_VOD_MEDIAKIT_API_KEY`
is configured; requires the `vod:read` scope in JWT mode. Only the principal
that created the task can poll it.

**`processing` is not a failure: poll again later and never resubmit the create
tool**, because each submission starts a new billed task.

### Input

| Field | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `task_id` | string | Yes | — | MediaKit task ID from a `vod_*` create tool; 1-256 chars of `A-Z a-z 0-9 . _ : -` |
| `operation` | `"enhance"` \| `"transcode"` \| `"separate_audio"` \| `"add_subtitles"` \| `"remove_subtitles"` | Yes | — | Echoed as `operation` by the create tool |
| `persist_output` | boolean | No | **`true`** | Requires background execution (`ark_job_submit` or native task augmentation); pass `false` for a foreground status check |
| `output_path` | string | No | — | Local file, or directory ending `/`; **a directory is required for `separate_audio`**, which writes every track. Stdio only, requires `persist_output=true`; see [Local Output Paths](#local-output-paths) |
| `overwrite` | boolean | No | `false` | Replace existing files at `output_path` |

### Output

Returns `VodTaskOutput`:

| Field | Type | Description |
|---|---|---|
| `provider` | `"byteplus-vod-mediakit"` | Provider surface |
| `task_id` | string | MediaKit task ID |
| `operation` | string | Operation that created the task |
| `status` | `"processing"` \| `"succeeded"` \| `"failed"` | Normalized status |
| `provider_status` | string \| null | Raw provider status label |
| `request_id` | string \| null | Provider diagnostic request ID |
| `created_at` / `finished_at` | string \| null | ISO-8601 timestamps |
| `duration_seconds` | number \| null | Output duration (input duration for `separate_audio`) |
| `resolution` | string \| null | Output resolution (enhance, transcode, subtitles) |
| `fps` | number \| null | Output frame rate (enhance) |
| `video_codec` | string \| null | Output video codec (transcode) |
| `tool_version` | string \| null | Enhancement tier (enhance) |
| `outputs` | list[TaskOutputFile] | Finished outputs; empty unless `succeeded` |
| `error` | TaskError \| null | `code` and safe `message`; set only when `failed` |

**TaskOutputFile** (shared with `gen_task_get`):

| Field | Type | Description |
|---|---|---|
| `role` | string | `video` (enhance, transcode, subtitles) or `voice` / `background` / `music` / `sfx` (`separate_audio`) |
| `artifact` | ArtifactRef \| null | Durable artifact when persisted; `local_path` set when also exported |
| `source_url` | string \| null | Expiring provider URL (about 24 hours), **kept even when persistence is skipped or fails** |
| `source_expires_at` | string \| null | ISO-8601 expected expiry of `source_url` |
| `persistence` | `"not_requested"` \| `"persisted"` \| `"failed"` | Persistence outcome, independent of task success |
| `persistence_issue` | ArtifactPersistenceIssue \| null | Safe explanation when persistence failed |

With `persist_output=true`, the first successful poll copies each output into
the artifact store **once per task**: concurrent polls share one copy and the
artifacts are cached by task ID for later polls. A persistence failure never
changes provider success.

### Task Statuses

| Status | Meaning |
|---|---|
| `processing` | Queued or running; poll again |
| `succeeded` | At least one entry in `outputs` |
| `failed` | `error` populated; `outputs` empty |

### Example

```json
// Foreground status check
{ "task_id": "amk-tool-transcode-video-112738623234", "operation": "transcode", "persist_output": false }

// Background retrieval of every separated track (submit through ark_job_submit)
{
  "task_id": "amk-tool-separate-voice-...",
  "operation": "separate_audio",
  "output_path": "/Users/me/out/stems/"
}

// Output (succeeded, separate_audio)
{
  "provider": "byteplus-vod-mediakit",
  "task_id": "amk-tool-separate-voice-...",
  "operation": "separate_audio",
  "status": "succeeded",
  "provider_status": "completed",
  "duration_seconds": 120.5,
  "outputs": [
    {
      "role": "voice",
      "artifact": { "id": "...", "uri": "seed-media://artifacts/...", "media_type": "audio", "mime_type": "audio/aac", "bytes": 1787924 },
      "source_url": "https://vod.ap-southeast-1.byteplusvod.com/voice.aac?sign=...",
      "persistence": "persisted"
    },
    {
      "role": "background",
      "artifact": { "id": "...", "uri": "seed-media://artifacts/...", "media_type": "audio", "mime_type": "audio/aac", "bytes": 1787924 },
      "source_url": "https://vod.ap-southeast-1.byteplusvod.com/background.aac?sign=...",
      "persistence": "persisted"
    }
  ]
}
```

---

## omnihuman_create_task / omnihuman_get_task / omnihuman_cancel_task

Registered when `BYTEPLUS_OMNIHUMAN_ENABLED=true` and `BYTEPLUS_CV_API_KEY` is
set. Scopes: `omnihuman:create`, `omnihuman:read`, `omnihuman:cancel`.

`omnihuman_create_task` input: `image` and `audio` (HTTPS URL or
`seed-media://artifacts/<id>`), optional `prompt` (max 2000 characters), and
`watermark` (default `true`). Output: `provider`, `status="accepted"`,
`task_id`, `request_id`, `recommended_poll_after_ms`.

`omnihuman_get_task` input: `task_id`, `persist_output` (default `true`, needs
task-augmented execution), `output_path`, `overwrite`. Output: `status`
(`queued`, `running`, `succeeded`, `failed`, `canceled`, `expired`,
`not_found`), `video` (durable `ArtifactRef`), `source_url` and
`source_expires_at` (one-hour provider URL), `persistence`,
`persistence_issue`, `error` (`code`, `message`, failed tasks only).

`omnihuman_cancel_task` input: `task_id`. Output: `canceled`, `message`
(explains why a started task cannot be canceled), `request_id`. See
[tools.md](tools.md#omnihuman_create_task--omnihuman_get_task--omnihuman_cancel_task)
for limits and artifact-reference behavior.

## Shared Types

### MediaSource

A media reference by URL or Base64.

| Field | Type | Required | Description |
|---|---|---|---|
| `kind` | `"url"` \| `"base64"` | Yes | Source type |
| `url` | string | If kind=url | HTTPS URL |
| `data` | string | If kind=base64 | Base64-encoded data |
| `mime_type` | string | No | MIME type |

Seedance image/audio inputs and the Seedream / Seed Audio `images` /
`image_reference` inputs (`ReferenceImageInput`) also accept
`url: "asset://<asset_id>"` for private asset library assets. Seedance passes
it through natively; Seedream and Seed Audio require
`BYTEPLUS_MODELARK_ASSET_REFERENCE_MODE=resolve`. See [assets.md](assets.md).

### AudioReference

An audio reference for voice cloning.

| Field | Type | Required | Description |
|---|---|---|---|
| `kind` | `"speaker"` \| `"url"` \| `"base64"` | Yes | Reference mode |
| `speaker_id` | string | If kind=speaker | Preset speaker ID |
| `url` | string | If kind=url | Reference audio URL, or `asset://<asset_id>` (resolve mode only) |
| `data` | string | If kind=base64 | Base64 audio data (WAV preflight-checked against 30s limit) |
| `mime_type` | string | No | MIME type |

### ArtifactRef

A durable reference to persisted media.

| Field | Type | Description |
|---|---|---|
| `id` | string | Unique artifact ID, or `provider-url` / `inline-fallback` when the output could not be stored |
| `uri` | string | `seed-media://artifacts/{id}`, or the temporary provider URL when `id="provider-url"` |
| `media_type` | `"image"` \| `"audio"` \| `"video"` \| `"three_d"` | Logical type |
| `mime_type` | string | e.g. `image/png`, `audio/wav`, `video/mp4` |
| `bytes` | integer | Size in bytes |
| `sha256` | string | SHA-256 hex digest |
| `created_at` | string | ISO-8601 timestamp |
| `expires_at` | string | Local artifact expiry |
| `source_expires_at` | string | Provider URL expiry (2h audio, 24h image/video/3D) |
| `persistence_error` | ArtifactPersistenceIssue \| null | Set when the output was generated (and billed) but could not be stored durably |
| `fallback_data` | string \| null | Base64 output bytes, only for `id="inline-fallback"` (up to `ARTIFACT_INLINE_FALLBACK_MAX_BYTES`) |
| `local_path` | string \| null | Absolute local path written for `output_path`/`output_dir` |
| `export_error` | string \| null | Why the requested local copy was not written; the durable artifact is unaffected |

### ArtifactPersistenceIssue

| Field | Type | Description |
|---|---|---|
| `code` | string | `untrusted_output_host`, `output_too_large`, `invalid_output_mime`, `source_expired`, `download_failed`, or `storage_failed` |
| `message` | string | Credential- and URL-safe failure message |
| `retryable` | boolean | Whether persistence may succeed if attempted again |
| `artifact_limit_bytes` | integer | Maximum size accepted by the durable artifact policy |
| `source_url_expires_at` | string \| null | When the unpersisted provider URL (the artifact's `uri`) expires |

Recover a `provider-url` artifact with `seed_media_persist_url` before
`source_url_expires_at`.

### VariationResult

Result of a single variation within a parallel generation.

| Field | Type | Description |
|---|---|---|
| `index` | integer | 0-based variation index |
| `seed` | integer \| null | Seed used (image only) |
| `artifact` | ArtifactRef \| null | Generated artifact (null if failed) |
| `task_id` | string \| null | Provider task ID for Seedance polling, obtained from the enclosing background result |
| `error` | VariationError \| null | Error details if failed |
| `request_id` | string \| null | Provider request ID |
| `provider_log_id` | string \| null | Provider log ID (Seed Audio) |

### VariationError

| Field | Type | Description |
|---|---|---|
| `code` | string | Error code, e.g. `QUEUE_TIMEOUT`, `TIMEOUT`, `GATHER_ERROR`, a provider code, or an upper-cased persistence code |
| `message` | string | Human-readable message |
| `request_id` | string \| null | Provider request ID, if available |
| `retryable` | boolean | Whether the variation may succeed if retried |
| `ambiguous_completion` | boolean | Whether the provider may have completed despite the error |
| `phase` | `"queued"` \| `"generating"` \| `"persisting"` \| null | Stage at failure: `queued` never started (safe to retry), `generating` provider call in flight, `persisting` output generated but not stored |

### VariationSummary

Aggregate result of a parallel generation.

| Field | Type | Description |
|---|---|---|
| `total` | integer | Total variations requested |
| `succeeded` | integer | Variations that produced output |
| `failed` | integer | Variations that failed |
| `variations` | list[VariationResult] | Per-variation results |

---

## 1. seed_audio_generate

Generate full-scene audio through Seed Speech.

### Input

| Field | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `text_prompt` | string | Yes | — | 1-3000 chars |
| `audio_references` | list[AudioReference] | No | `[]` | Max 3 |
| `image_reference` | MediaSource | No | — | Mutually exclusive with audio |
| `output` | AudioOutputOptions | No | — | Format, rate, pitch |
| `watermark` | AudioWatermarkOptions | No | — | AIGC watermark |
| `persist` | boolean | No | `true` | Persist to artifact store |
| `output_path` | string | No | — | Local file or directory (ending `/`); stdio only, requires `persist=true` |
| `overwrite` | boolean | No | `false` | Replace an existing `output_path` file |

### AudioOutputOptions

| Field | Type | Default | Constraints |
|---|---|---|---|
| `format` | `"wav"` \| `"mp3"` \| `"pcm"` \| `"ogg"` | — | — |
| `sample_rate` | integer | — | 8000-48000 |
| `speech_rate` | integer | — | -50 to 100 |
| `loudness_rate` | integer | — | -50 to 100 |
| `pitch_rate` | integer | — | -12 to 12 |
| `subtitle` | boolean | — | — |
| `subtitle_type` | `"utterance"` \| `"word"` | — | — |

### Output

| Field | Type | Description |
|---|---|---|
| `provider` | `"byteplus-seed-speech"` | Fixed |
| `model` | `"seed-audio-1.0"` | Fixed |
| `duration_seconds` | float | Output duration |
| `billing_duration_seconds` | float | Billed duration |
| `artifact` | ArtifactRef | Persisted audio |
| `subtitle` | Subtitle \| null | Optional subtitles |
| `request_id` | string | Request ID |
| `provider_log_id` | string \| null | X-Tt-Logid |

### Example

```json
// Input
{
  "text_prompt": "Welcome to BytePlus.",
  "output": { "format": "wav", "sample_rate": 44100 }
}

// Output
{
  "provider": "byteplus-seed-speech",
  "model": "seed-audio-1.0",
  "duration_seconds": 2.5,
  "billing_duration_seconds": 2.5,
  "artifact": {
    "id": "5828e515-...",
    "uri": "seed-media://artifacts/5828e515-...",
    "media_type": "audio",
    "mime_type": "audio/wav",
    "bytes": 2656078
  },
  "request_id": "",
  "provider_log_id": "20260721..."
}
```

---

## 2. seed_audio_generate_variations

Generate N independent audio variations in parallel.

### Input

| Field | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `text_prompt` | string | No* | — | 1-3000 chars |
| `variations` | integer | No | 1 | 1-5 |
| `variation_prompts` | list[string] | No | — | Must have `variations` entries |
| `audio_references` | list[AudioReference] | No | `[]` | Max 3 |
| `image_reference` | MediaSource | No | — | Mutually exclusive with audio |
| `output` | AudioOutputOptions | No | — | — |
| `watermark` | AudioWatermarkOptions | No | — | — |
| `persist` | boolean | No | `true` | — |
| `output_dir` | string | No | — | Local directory; stdio only, requires `persist=true` |
| `overwrite` | boolean | No | `false` | Replace existing files in `output_dir` |

\* Either `text_prompt` or `variation_prompts` must be provided.

### Output

| Field | Type | Description |
|---|---|---|
| `provider` | `"byteplus-seed-speech"` | Fixed |
| `model` | `"seed-audio-1.0"` | Fixed |
| `summary` | VariationSummary | Aggregate results |

### Example

```json
// Input
{
  "text_prompt": "Hello world",
  "variations": 3,
  "persist": true
}

// Output
{
  "provider": "byteplus-seed-speech",
  "model": "seed-audio-1.0",
  "summary": {
    "total": 3,
    "succeeded": 3,
    "failed": 0,
    "variations": [
      { "index": 0, "artifact": { "id": "...", "media_type": "audio", ... } },
      { "index": 1, "artifact": { "id": "...", "media_type": "audio", ... } },
      { "index": 2, "artifact": { "id": "...", "media_type": "audio", ... } }
    ]
  }
}
```

---

## 3. seedream_generate_image

Generate or edit an image through ModelArk Seedream.

### Input

| Field | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `prompt` | string | Yes | — | — |
| `images` | list[MediaSource] | No | — | Reference images for editing |
| `model` | string | No | Configured model | Must be in capability registry |
| `size` | string | No | — | e.g. "1024x1024" |
| `seed` | integer | No | — | -1 = random, 0+ = fixed |
| `max_images` | integer | No | — | 1-15 (batch-capable models only) |
| `output_format` | `"png"` \| `"jpeg"` | No | — | Model-dependent |
| `response_format` | `"url"` \| `"b64_json"` | No | — | — |
| `watermark` | boolean | No | — | AIGC watermark |
| `prompt_optimization` | `"standard"` \| `"fast"` | No | `SEEDREAM_PROMPT_OPTIMIZATION_MODE` on 5.0 Pro | Default `standard` |
| `persist` | boolean | No | `true` | — |
| `output_path` | string | No | — | Local file, or directory ending `/` (required when `max_images > 1`); stdio only, requires `persist=true` |
| `overwrite` | boolean | No | `false` | Replace an existing `output_path` file |

### Output

| Field | Type | Description |
|---|---|---|
| `provider` | `"byteplus-modelark"` | Fixed |
| `model` | string | Model used |
| `created_at` | string | ISO-8601 |
| `artifacts` | list[ArtifactRef] | Persisted images; an image that could not be stored is returned as a `provider-url` or `inline-fallback` reference with `persistence_error` instead of failing the call |
| `item_errors` | list[SeedreamItemError] | Per-item failures |
| `usage` | SeedreamUsage | Token usage |

### Example

```json
// Input
{
  "prompt": "A serene mountain landscape at sunset",
  "size": "1024x1024",
  "seed": 42,
  "output_format": "jpeg"
}

// Output
{
  "provider": "byteplus-modelark",
  "model": "dola-seedream-5-0-pro-260628",
  "created_at": "2026-07-21T05:36:04+00:00",
  "artifacts": [
    {
      "id": "83ef8c61-...",
      "uri": "seed-media://artifacts/83ef8c61-...",
      "media_type": "image",
      "mime_type": "image/jpeg",
      "bytes": 428968
    }
  ],
  "item_errors": [],
  "usage": { "total_tokens": 4096 }
}
```

---

## 4. seedream_generate_image_variations

Generate N independent image variations in parallel with distinct seeds.

### Input

| Field | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `prompt` | string | No* | — | — |
| `variations` | integer | No | 1 | 1-10 |
| `variation_prompts` | list[string] | No | — | Must have `variations` entries |
| `base_seed` | integer | No | — | -1 to 2147483647 |
| `images` | list[MediaSource] | No | — | Reference images |
| `model` | string | No | Configured | — |
| `size` | string | No | — | — |
| `output_format` | `"png"` \| `"jpeg"` | No | — | — |
| `response_format` | `"url"` \| `"b64_json"` | No | — | — |
| `watermark` | boolean | No | — | — |
| `prompt_optimization` | `"standard"` \| `"fast"` | No | `SEEDREAM_PROMPT_OPTIMIZATION_MODE` on 5.0 Pro | Default `standard` |
| `persist` | boolean | No | `true` | — |
| `output_dir` | string | No | — | Local directory; stdio only, requires `persist=true` |
| `overwrite` | boolean | No | `false` | Replace existing files in `output_dir` |

\* Either `prompt` or `variation_prompts` must be provided.

### Seed Behavior

| `base_seed` | Per-variation seeds |
|---|---|
| `null` | Provider randomizes (not recorded) |
| `-1` | Client picks random (recorded) |
| `N` | `[N, N+1, N+2, ...]` (deterministic, modulo 2^31) |

### Output

| Field | Type | Description |
|---|---|---|
| `provider` | `"byteplus-modelark"` | Fixed |
| `model` | string | Model used |
| `created_at` | string | ISO-8601 |
| `summary` | VariationSummary | Aggregate results |

### Example

```json
// Input
{
  "prompt": "A futuristic city skyline, cyberpunk",
  "variations": 3,
  "base_seed": 42,
  "size": "1024x1024"
}

// Output
{
  "provider": "byteplus-modelark",
  "model": "dola-seedream-5-0-pro-260628",
  "created_at": "2026-07-21T...",
  "summary": {
    "total": 3,
    "succeeded": 3,
    "failed": 0,
    "variations": [
      { "index": 0, "seed": 42, "artifact": { "id": "16cfa323-...", ... } },
      { "index": 1, "seed": 43, "artifact": { "id": "86f189f2-...", ... } },
      { "index": 2, "seed": 44, "artifact": { "id": "6f5978c9-...", ... } }
    ]
  }
}
```

---

## 5. seedance_create_task

**One create tool for every configured Seedance model** (2.0, 2.0 Fast, 2.0
Mini, 2.5, and 2.5 Premium): single tasks, up to five parallel variations,
Draft mode, and dry runs. Registered whenever `BYTEPLUS_MODELARK_API_KEY` is
set; requires `seedance:create` in JWT mode. **Required background
submission**: run it through `ark_job_submit` (or native task augmentation).
Poll the returned task IDs with [`gen_task_get`](#7-gen_task_get).

### Model Selection

Set `model` to an **exact configured ID from `ark_list_models(kind='video')`**;
every video model listed there names `seedance_create_task` as its tool. Omit
`model` to use `SEEDANCE_DEFAULT_MODEL`, which defaults to
`dreamina-seedance-2-5-260628` (Seedance 2.5). Seedance 2.5 Premium
(`dreamina-seedance-2-5-premium-260915`, `SEEDANCE_2_5_PREMIUM_MODEL`) is just
another model ID, bound automatically only when
`BYTEPLUS_MODELARK_SEEDANCE_2_5_PREMIUM_ENABLED=true` (whitelist-only).

Family limits as shipped (**`ark_list_models(kind='video')` is authoritative**
for the models configured on a given server):

| Family | Resolutions | Duration (s) | Max images / videos / audios | Draft (final resolution) | Audio-only input | `omni_reference_task_type` | `output_format` |
|---|---|---|---|---|---|---|---|
| `seedance_2` | 480p, 720p, 1080p, 4k | 4-15 | 9 / 3 / 3 | No | No | — | — |
| `seedance_2_fast` | 480p, 720p | 4-15 | 9 / 3 / 3 | No | No | — | — |
| `seedance_2_mini` | 480p, 720p | 4-15 | 9 / 3 / 3 | No | No | — | — |
| `seedance_2_5` | 480p, 720p, 1080p | 4-30 | 30 / 10 / 10 | Yes (1080p) | Yes | `auto`, `reference`, `edit`, `extend` | `mp4`, `mov` |
| `seedance_2_5_premium` (whitelist + flag) | 480p, 720p, 1080p, 4k | 4-30 | 30 / 10 / 10 | Yes (4k) | Yes | `auto`, `reference`, `edit`, `extend` | `mp4`, `mov` |

### Input

| Field | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `prompt` | string | No* | — | 1-32,000 chars. Optional when media or `variation_prompts` are given |
| `images` | list[SeedanceImageInput] | No | — | Schema max 30; per-model max checked at runtime. Roles `first_frame`, `last_frame`, `reference_image` |
| `videos` | list[SeedanceVideoInput] | No | — | Schema max 10; per-model max checked at runtime |
| `audios` | list[SeedanceAudioInput] | No | — | Schema max 10; per-model max checked at runtime. 2.5 models accept audio-only input; 2.0 models need an image or video too |
| `model` | string | No | `SEEDANCE_DEFAULT_MODEL` | Exact configured ID from `ark_list_models(kind='video')`. With `draft_task_id`, omit to reuse the Draft task's model |
| `resolution` | `"480p"` \| `"720p"` \| `"1080p"` \| `"4k"` | No | — | Per-model set checked at runtime. `draft=true` allows only `480p` (the default); `draft_task_id` allows only the model's Draft final resolution (the default) |
| `ratio` | string | No | — | `16:9`, `4:3`, `1:1`, `3:4`, `9:16`, `21:9`, or `adaptive` (provider default). Edit, extend, first-frame, and first/last-frame requests on 2.5 require it omitted or `adaptive` |
| `duration` | integer | No | — | -1 (auto) or 4 up to the model max (15 for 2.0, 30 for 2.5). `edit` requires -1 or omitted |
| `omni_reference_task_type` | `"auto"` \| `"reference"` \| `"edit"` \| `"extend"` | No | — | 2.5 models only. `edit`/`extend` need at least one reference video |
| `output_format` | `"mp4"` \| `"mov"` | No | — | 2.5 models only. `mov` is H.264, YUV 4:4:4, PCM audio for post-production; some players cannot play it |
| `generate_audio` | boolean | No | — | Generate an audio track |
| `watermark` | boolean | No | `false` | AIGC watermark |
| `return_last_frame` | boolean | No | — | Return the last frame as a separate image output |
| `execution_expires_after` | integer | No | — | 3600-259200 seconds |
| `priority` | integer | No | — | 0-9; model range checked at runtime |
| `safety_identifier` | string | No | — | Max 64 chars |
| `draft` | boolean | No | — | Draft step 1 (2.5 models): 480p preview billed as a normal 480p video. Mutually exclusive with `draft_task_id` |
| `draft_task_id` | string | No | — | Draft step 2: render a succeeded Draft task as the final video. See [Draft Mode](#draft-mode) |
| `variations` | integer | No | `1` | 1-5. Each variation is a **separate billed task** |
| `variation_prompts` | list[string] | No | — | Exactly `variations` entries, each 1-32,000 chars; overrides `prompt` |
| `dry_run` | boolean | No | `false` | Validate and return provider request bodies plus a cost estimate; **nothing is submitted or billed** |

\* At least one of `prompt`, `images`, `videos`, or `audios` is required
(unless `draft_task_id` is set). `variations > 1` needs `prompt` or
`variation_prompts`.

### Runtime Validation

The schema stays permissive; each request is checked against the resolved
model's limits from the capability registry (resolution, ratio, duration
range, priority and expiry ranges, reference counts, Draft support,
audio-only input, task types, `output_format`). **Every violation is returned
in one error** before anything is submitted:

```text
Request is not valid for model '<id>' (<family>):
- <violation>
- <violation>
Call ark_list_models(kind='video') for per-model limits.
```

`asset://` references are preflighted against the private asset library
before submission, including on dry runs.

#### Auto-locked parameters by task type

When the provider detects (or is hinted via `omni_reference_task_type`)
that the task is video editing, extension, or first/last-frame generation,
certain parameters are auto-derived from the input media and cannot be
overridden:

| Task type | Aspect ratio | Duration |
|---|---|---|
| Video editing | Locked to input video's ratio | Locked to input video's duration (±0.3s) |
| Video extension | Locked to input video's ratio | Set freely |
| First/last-frame generation | Locked to first image's ratio | Set freely |
| Text-to-video / standard reference | Set freely | Set freely (or `-1` for auto) |

### Draft Mode

**Two steps, Seedance 2.5 and 2.5 Premium only:**

1. **Preview.** Call with `draft: true`. `resolution` must be omitted (becomes
   `480p`) or `480p`. Works with `variations` to compare several previews.
2. **Final.** Once a Draft has succeeded, call again with `draft_task_id` set
   to its `task_id`. The tool checks that the caller owns the task, that it
   succeeded, that it was created in Draft mode, and that it is at most 7 days
   old.
   - The provider reuses the Draft's model, prompt, media, duration, ratio,
     seed, `generate_audio`, and `omni_reference_task_type`, so `prompt`,
     `images`, `videos`, `audios`, `duration`, `ratio`, `generate_audio`,
     `omni_reference_task_type`, and `draft` **must be omitted** (they are
     rejected even when they match).
   - `model` may be omitted (the Draft's model is used); a different model is
     rejected.
   - `resolution` must be omitted or the model's Draft final resolution
     (`1080p` for 2.5, `4k` for Premium), which is the default.
   - `return_last_frame`, `watermark`, `execution_expires_after`, `priority`,
     `safety_identifier`, and `output_format` may be set again.
   - Requires `variations=1` and no `variation_prompts`.

### Variations and Dry Run

- **Variations:** `variations` 2-5 creates that many provider tasks in
  parallel. Partial failures are reported per entry in `tasks` (with `error`)
  rather than failing the call.
- **Dry run:** `dry_run: true` runs all validation and returns `preview`
  instead of submitting. Through `ark_job_submit`, a dry run finishes on the
  first `ark_job_get`.

### SeedanceImageInput

Extends `MediaSource` with a `role` field. A bare URL string,
`"asset://<asset_id>"`, or `{"url": "..."}` is accepted and defaults to
`role: "reference_image"`.

| Field | Type | Description |
|---|---|---|
| `role` | `"first_frame"` \| `"last_frame"` \| `"reference_image"` | Image purpose |

### SeedanceVideoInput

Video references are URL-only. Unlike image and audio references, there is no
Base64 path — videos must be uploaded to a publicly reachable HTTPS endpoint
before the tool is called, or referenced as `asset://<asset_id>` for an Active
private Video asset. Use the `media_upload` tool to upload Base64 or a local
file (stdio only) to object storage (TOS or S3) and receive a presigned HTTPS
GET URL. Alternatively, host the video on your own HTTPS endpoint. The URL
must resolve to a public IP (loopback, private, and link-local addresses are
rejected by the SSRF policy). A bare URL string or `{"url": "..."}` is
accepted.

| Field | Type | Description |
|---|---|---|
| `kind` | `"url"` | Always `url` (hard-coded) |
| `url` | string | Public HTTPS URL of the reference video, or `asset://<asset_id>` |
| `role` | `"reference_video"` | Always `reference_video` |

### SeedanceAudioInput

Extends `MediaSource`; also accepts a bare URL string, `"asset://<asset_id>"`,
or `{"url": "..."}`.

| Field | Type | Description |
|---|---|---|
| `role` | `"reference_audio"` | Always `reference_audio` |

### Output

Returns `SeedanceCreateTaskOutput`, **the same shape for single requests,
variations, and dry runs**:

| Field | Type | Description |
|---|---|---|
| `model` | string | Resolved model ID |
| `family` | string | Resolved model family (e.g. `seedance_2_5`) |
| `dry_run` | boolean | `true` when nothing was submitted; see `preview` |
| `tasks` | list[SeedanceTaskRef] | One entry per requested variation, in order; empty for a dry run |
| `submitted` | integer | Tasks the provider accepted |
| `failed` | integer | Submissions that failed |
| `recommended_poll_after_ms` | integer \| null | Suggested delay before the first `gen_task_get` poll; `null` for a dry run |
| `preview` | SeedanceCreatePreview \| null | Set only for a dry run |

**SeedanceTaskRef:**

| Field | Type | Description |
|---|---|---|
| `index` | integer | 0-based variation index (`0` for a single request) |
| `task_id` | string \| null | Provider task ID; poll with `gen_task_get`. For `draft=true`, pass it as `draft_task_id` once succeeded. `null` when submission failed |
| `request_id` | string \| null | Provider request ID, when returned |
| `error` | VariationError \| null | Why this submission failed; `null` when accepted |

**SeedanceCreatePreview:**

| Field | Type | Description |
|---|---|---|
| `requests` | list[object] | Provider request bodies, one per variation. HTTPS media URLs are reduced to scheme and host, inline Base64 to its size; `asset://` references are kept |
| `estimated_cost_usd` | number | Static estimate from the server's price table for all requests; **not a provider quote** |

### Polling

Read each `tasks[].task_id` from the background result and pass **all of them
in one call** to [`gen_task_get`](#7-gen_task_get). **`queued` or `running` is
not a failure: poll with `gen_task_get`; never resubmit `seedance_create_task`
to poll**, because every submission is a new billed task.

### Examples

```json
// Single task (default model), via ark_job_submit
{
  "tool_name": "seedance_create_task",
  "arguments": {
    "input": {
      "prompt": "A cat walking through a garden",
      "images": ["https://example.com/cat.png"],
      "resolution": "1080p",
      "duration": 10
    }
  }
}

// Background result
{
  "model": "dreamina-seedance-2-5-260628",
  "family": "seedance_2_5",
  "dry_run": false,
  "tasks": [
    { "index": 0, "task_id": "cgt-20260721134956-h5cz9", "request_id": "021...", "error": null }
  ],
  "submitted": 1,
  "failed": 0,
  "recommended_poll_after_ms": 5000,
  "preview": null
}
```

```json
// Variations on a Seedance 2.0 model (arguments.input)
{
  "model": "dreamina-seedance-2-0-260128",
  "variations": 2,
  "variation_prompts": [
    "The cat walks forward slowly",
    "The cat jumps playfully"
  ],
  "images": [
    { "kind": "base64", "data": "...", "mime_type": "image/png", "role": "reference_image" }
  ],
  "resolution": "480p",
  "duration": 5
}

// Background result (one variation failed)
{
  "model": "dreamina-seedance-2-0-260128",
  "family": "seedance_2",
  "dry_run": false,
  "tasks": [
    { "index": 0, "task_id": "cgt-...-rq5gm", "request_id": "021...", "error": null },
    {
      "index": 1,
      "task_id": null,
      "request_id": null,
      "error": { "code": "TIMEOUT", "message": "...", "retryable": true, "ambiguous_completion": true, "phase": "generating" }
    }
  ],
  "submitted": 1,
  "failed": 1,
  "recommended_poll_after_ms": 5000,
  "preview": null
}
```

```json
// Draft step 1: 480p preview (arguments.input)
{ "prompt": "A paper boat drifting down a rainy street", "draft": true }

// Draft step 2: final render from the succeeded Draft (arguments.input)
{ "draft_task_id": "cgt-20260721134956-h5cz9" }
```

```json
// Dry run (arguments.input)
{ "prompt": "A lighthouse at dusk", "variations": 2, "dry_run": true }

// Background result (available on the first ark_job_get)
{
  "model": "dreamina-seedance-2-5-260628",
  "family": "seedance_2_5",
  "dry_run": true,
  "tasks": [],
  "submitted": 0,
  "failed": 0,
  "recommended_poll_after_ms": null,
  "preview": {
    "requests": [
      { "model": "dreamina-seedance-2-5-260628", "content": [{ "type": "text", "text": "A lighthouse at dusk" }], "watermark": false },
      { "model": "dreamina-seedance-2-5-260628", "content": [{ "type": "text", "text": "A lighthouse at dusk" }], "watermark": false }
    ],
    "estimated_cost_usd": 1.2
  }
}
```

The `estimated_cost_usd` value above is illustrative only.

---

## 7. gen_task_get

**One poller for every ModelArk generation task**: Seedance video tasks
(`seedance_create_task`, every Seedance model) and Seed 3D tasks
(`hyper3d_create_task`, `hitem3d_create_task`). Registered whenever ModelArk is
configured; requires `gen:read` in JWT mode. Optional background task (required
when `persist_output=true`).

Fetches tasks with one provider list call per page of 20 task IDs (at most
three pages), filtered by task ID, and re-fetches individually only tasks
missing from the list or succeeded without an output URL. Task IDs the caller
did not create are reported in `errors` instead of failing the call.

### Polling Workflow

1. Run a create tool through `ark_job_submit` and read each provider `task_id`
   from the background result.
2. Call `gen_task_get` in the foreground with **every** task ID you are waiting
   on (`persist_output=false`, the default).
3. While `all_terminal` is `false`, wait `recommended_poll_after_ms` (5000 ms
   while any task is active) and poll again. **`queued` or `running` is not a
   failure; never resubmit the create tool to retry polling**, because that
   creates a second billed task.
4. Once `all_terminal` is `true`, call `gen_task_get` again through
   `ark_job_submit` with `persist_output=true` (optionally `output_dir`) to keep
   the outputs as durable artifacts before the provider URLs expire (about 24
   hours).

### Input

| Field | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `task_ids` | list[string] | Yes | — | 1-50 ModelArk task IDs (not `ark_job` IDs); duplicates ignored |
| `persist_output` | boolean | No | **`false`** | Persist each succeeded task's outputs once per task; `true` requires background execution (`ark_job_submit` or native task augmentation) |
| `output_dir` | string | No | — | Local directory; stdio only, requires `persist_output=true`; see [Local Output Paths](#local-output-paths) |
| `overwrite` | boolean | No | `false` | Replace existing files in `output_dir` |

### Output

Returns `GenTaskGetOutput`:

| Field | Type | Description |
|---|---|---|
| `tasks` | list[GenTask] | One entry per found task, in request order |
| `errors` | list[GenTaskLookupError] | `task_id`, `code` (`NOT_OWNED`, `NOT_FOUND`, or a provider code), `message` |
| `all_terminal` | boolean | `true` when every found task has finished, so polling can stop |
| `active_tasks` | integer | Found tasks still queued or running |
| `recommended_poll_after_ms` | integer \| null | `5000` while any task is active; `null` when `all_terminal` |

**GenTask:**

| Field | Type | Description |
|---|---|---|
| `task_id` | string | Provider task ID |
| `model` | string | Model ID that ran the task |
| `family` | string \| null | Registry family of the model; `null` when the model is not configured on this server |
| `kind` | `"video"` \| `"3d"` \| `"unknown"` | Output kind from the registry binding for `model` |
| `status` | GenTaskStatus | `queued`, `running`, `succeeded`, `failed`, `cancelled`, or `expired` |
| `created_at` / `updated_at` | string | ISO-8601 |
| `outputs` | list[TaskOutputFile] | `video` and `last_frame` for video tasks, `model_file` for 3D tasks; empty until `succeeded`. Same shape as in [`vod_get_task`](#vod_get_task) |
| `error` | TaskError \| null | Provider `code` and `message` when failed |
| `usage` | TaskUsage \| null | `prompt_tokens`, `completion_tokens`, `total_tokens` when reported |
| `draft` | boolean \| null | `true` for a Seedance Draft task (480p preview); once it has succeeded, pass its `task_id` as `draft_task_id` within 7 days of `created_at` |
| `settings` | SeedanceTaskSettings \| null | Video generation settings echoed by the provider; `null` for 3D tasks |
| `queue` | SeedanceQueueInfo \| null | Server-derived queue timing for a queued or running video task; `null` once finished and for 3D tasks |

Without persistence, each output carries its expiring `source_url` and
`persistence="not_requested"`. With `persist_output=true`, outputs are copied
**once per task** under a single-flight lock, so overlapping polls never
download the same file twice; a storage failure returns the provider URL with
`persistence="failed"` and a later poll retries.

### SeedanceQueueInfo

ModelArk publishes no queue position or ETA. These values are derived by the
server from the task's timestamps and expiry setting; they are estimates, not a
provider SLA.

| Field | Type | Description |
|---|---|---|
| `queued_seconds` | integer \| null | Now minus `created_at` while queued; `updated_at` minus `created_at` once running (approximate) |
| `running_seconds` | integer \| null | Seconds since the last status update while running (approximate) |
| `service_tier` | string \| null | `default` or `flex` |
| `expires_at` | string \| null | `created_at + execution_expires_after`: the provider fails the task if unfinished by then |
| `expires_at_estimated` | boolean | `true` when `execution_expires_after` was not reported and the 48-hour default was assumed |
| `hint` | string \| null | Human-readable summary |

### Task Statuses

| Status | Meaning |
|---|---|
| `queued` | Waiting to start |
| `running` | Generating |
| `succeeded` | Completed, outputs available |
| `failed` | Failed, check `error` |
| `expired` | Expired before completion |
| `cancelled` | Was cancelled |

### Example

```json
// Input (foreground poll)
{ "task_ids": ["cgt-20260721134956-h5cz9", "cgt-20260721135012-k2m8p"] }

// Output (one still running)
{
  "tasks": [
    {
      "task_id": "cgt-20260721134956-h5cz9",
      "model": "dreamina-seedance-2-0-260128",
      "family": "seedance_2",
      "kind": "video",
      "status": "succeeded",
      "created_at": "2026-07-21T06:02:19+00:00",
      "updated_at": "2026-07-21T06:06:13+00:00",
      "outputs": [
        {
          "role": "video",
          "source_url": "https://ark-content-generation.example.com/video.mp4?sign=...",
          "source_expires_at": "2026-07-22T06:06:20+00:00",
          "persistence": "not_requested"
        }
      ],
      "usage": { "completion_tokens": 48400, "total_tokens": 48400 }
    },
    {
      "task_id": "cgt-20260721135012-k2m8p",
      "model": "dreamina-seedance-2-0-260128",
      "family": "seedance_2",
      "kind": "video",
      "status": "running",
      "created_at": "2026-07-21T06:02:35+00:00",
      "updated_at": "2026-07-21T06:03:10+00:00",
      "outputs": []
    }
  ],
  "errors": [],
  "all_terminal": false,
  "active_tasks": 1,
  "recommended_poll_after_ms": 5000
}

// Input (background persistence, via ark_job_submit)
{ "task_ids": ["cgt-20260721134956-h5cz9"], "persist_output": true }
```

---

## 8. gen_task_list

List the caller's ModelArk generation tasks (Seedance video and 3D), **newest
first**, covering tasks created in the **last 7 days** (the provider's list
window). Registered whenever ModelArk is configured; requires `gen:read` in JWT
mode. Use `gen_task_get` for outputs.

### Input

| Field | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `page_size` | integer | No | `20` | 1-100 |
| `cursor` | string | No | — | Opaque `next_cursor` from a previous call made with the same filters |
| `status` | GenTaskStatus | No | — | `queued`, `running`, `succeeded`, `failed`, `cancelled`, or `expired` |
| `model` | string | No | — | Exact model ID |
| `task_ids` | list[string] | No | — | Up to 100; IDs the caller does not own are skipped |

### Output

Returns `GenTaskPage`:

| Field | Type | Description |
|---|---|---|
| `tasks` | list[GenTaskSummary] | Matching tasks, newest first: `task_id`, `model`, `kind`, `status`, `created_at`, and `error` when failed |
| `next_cursor` | string \| null | Pass back as `cursor` to continue; `null` when there are no more tasks |

### Paging Rules

- **Follow `next_cursor` until it is `null`.** A page may hold **fewer than
  `page_size` tasks while `next_cursor` is non-null**, so a short page does not
  mean the list is finished.
- **Keep the same filters while paging.** A cursor is bound to `status`,
  `model`, `task_ids`, and `page_size`; changing any of them rejects the
  cursor. Repeat the original filters or start again without a cursor.
- Non-local principals page through their owned task IDs from the ownership
  store in slices of 100, so the provider request URL never overflows however
  many tasks the caller owns.

### Example

```json
// Input
{ "status": "succeeded", "page_size": 10 }

// Output
{
  "tasks": [
    {
      "task_id": "cgt-20260721134956-h5cz9",
      "model": "dreamina-seedance-2-0-260128",
      "kind": "video",
      "status": "succeeded",
      "created_at": "2026-07-21T06:02:19+00:00"
    }
  ],
  "next_cursor": null
}
```

---

## 9. gen_task_cancel_or_delete

Cancel (queued) or delete (terminal) any ModelArk generation task: Seedance,
Hyper3D, or Hitem3d. Registered whenever ModelArk is configured; requires
`gen:delete` in JWT mode. **Destructive.** The tool first fetches the task and
refuses to act when its actual status differs from `expected_status`. A deleted
task's outputs are no longer retrievable through `gen_task_get`.

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `task_id` | string | Yes | ModelArk generation task ID |
| `mode` | `"cancel"` \| `"delete"` | Yes | Operation |
| `expected_status` | `"queued"` \| `"succeeded"` \| `"failed"` \| `"expired"` | Yes | Expected current status |
| `confirm` | `true` | Yes | Explicit confirmation |

### DELETE Semantics

| Status | Cancel | Delete |
|---|---|---|
| `queued` | Yes | No |
| `running` | No | No |
| `succeeded` | No | Yes |
| `failed` | No | Yes |
| `expired` | No | Yes |
| `cancelled` | No | No |

### Output

| Field | Type | Description |
|---|---|---|
| `task_id` | string | Task ID |
| `mode` | `"cancel"` \| `"delete"` | Operation performed |
| `previous_status` | string | Status before operation |
| `message` | string | Result message |

---

## Resources

### seed-media://artifacts/{artifact_id}

Returns persisted media by artifact ID with the correct MIME type.

```json
{
  "contents": [
    {
      "content": "<base64-encoded-bytes>",
      "mime_type": "image/png"
    }
  ],
  "meta": {
    "artifact_id": "83ef8c61-...",
    "media_type": "image"
  }
}
```

### seed-health://status

Returns server health and configuration status as plain text.

## 10. media_upload

Upload media to object storage (TOS or S3) and return a presigned HTTPS GET
URL. Registered when the selected object-storage backend credentials are set
(`TOS_*` with default `OBJECT_STORAGE_BACKEND=tos`, or `S3_*` with
`OBJECT_STORAGE_BACKEND=s3`).

### Input

| Field | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `media_type` | `"image"` \| `"audio"` \| `"video"` | yes | — | Media category |
| `mime_type` | string | yes | — | Must be in the allowed MIME list for the category |
| `data` | string | one of | — | Base64-encoded bytes. Mutually exclusive with `file_path` |
| `file_path` | string | one of | — | Local file path (stdio only). Mutually exclusive with `data` |
| `key_prefix` | string | no | `"references"` | Alphanumeric, `-`, `_`, `/` only |
| `expires_in_seconds` | integer | no | configured TTL | Presigned URL validity, 60–604800. Use a long value (e.g. 3600) for VOD inputs that are fetched asynchronously |

### Output

| Field | Type | Description |
|---|---|---|
| `url` | string | Presigned HTTPS GET URL |
| `expires_at` | string | ISO-8601 expiry timestamp |
| `object_key` | string | Object key |
| `bytes` | integer | Uploaded byte count |

### Example

```json
// Input
{
  "media_type": "video",
  "mime_type": "video/mp4",
  "data": "AAAAIGZ0cBAAA..."
}

// Output
{
  "url": "https://test-bucket.tos-ap-southeast-1.bytepluses.com/references/video/abc?X-Tos-Signature=...",
  "expires_at": "2026-07-24T07:30:00+00:00",
  "object_key": "references/video/abc",
  "bytes": 1048576
}
```

---

## 11. media_presign

Generate a fresh presigned HTTPS GET URL for an existing object in storage
(TOS or S3) without re-uploading. Use this when a previously uploaded
reference's presigned URL has expired or is about to expire. The object must
already exist in the bucket (uploaded via `media_upload`). No data is
transferred — only a new URL is minted.

### Input

| Field | Type | Required | Constraints |
|---|---|---|---|
| `object_key` | string | yes | Object key from a prior `media_upload` call. Alphanumeric, `-`, `_`, `/`; first char must be alphanumeric |
| `expires_in_seconds` | integer | no | Presigned URL validity, 60–604800. Use a long value (e.g. 3600) for VOD inputs that are fetched asynchronously |

### Output

| Field | Type | Description |
|---|---|---|
| `url` | string | Fresh presigned HTTPS GET URL |
| `expires_at` | string | ISO-8601 expiry timestamp |
| `object_key` | string | Object key the URL grants access to |

### Example

```json
// Input
{
  "object_key": "references/video/abc-123-def"
}

// Output
{
  "url": "https://test-bucket.tos-ap-southeast-1.bytepluses.com/references/video/abc-123-def?X-Tos-Signature=...",
  "expires_at": "2026-07-24T07:30:00+00:00",
  "object_key": "references/video/abc-123-def"
}
```

---

## 12. media_presign_batch

Generate fresh presigned HTTPS GET URLs for many existing objects in storage
(TOS or S3) in a single round-trip. Accepts a list of object keys from prior
`media_upload` calls and returns a presigned URL for each. No data is
transferred — only new URLs are minted. A malformed, unowned, or
provider-failing key is reported inline as a per-key error while the rest of
the batch succeeds.

### Input

| Field | Type | Required | Constraints |
|---|---|---|---|
| `object_keys` | list[string] | yes | Object keys from prior `media_upload` calls (1–100 entries). Alphanumeric, `-`, `_`, `/`; first char must be alphanumeric |
| `expires_in_seconds` | integer | no | Presigned URL validity applied to every key, 60–604800. Use a long value (e.g. 3600) for VOD inputs that are fetched asynchronously |

### Output

| Field | Type | Description |
|---|---|---|
| `items` | list[MediaPresignBatchItem] | Per-key results, in input order |
| `succeeded` | integer | Number of keys that produced a presigned URL |
| `failed` | integer | Number of keys that failed |

**MediaPresignBatchItem:**

| Field | Type | Description |
|---|---|---|
| `object_key` | string | Object key this entry corresponds to |
| `url` | string \| null | Fresh presigned HTTPS GET URL (null if failed) |
| `expires_at` | string \| null | ISO-8601 expiry timestamp (null if failed) |
| `code` | string \| null | Machine-readable error code (`INVALID_KEY`, `NOT_OWNED`, `INTERNAL`, or provider code) |
| `error` | string \| null | Human-readable error message (null if succeeded) |
| `request_id` | string \| null | Provider request ID for this key's presign call (null if no provider call or not available) |

### Example

```json
// Input
{
  "object_keys": ["references/video/abc-123-def", "references/image/def-456-ghi"]
}

// Output
{
  "items": [
    {
      "object_key": "references/video/abc-123-def",
      "url": "https://test-bucket.tos-ap-southeast-1.bytepluses.com/references/video/abc-123-def?X-Tos-Signature=...",
      "expires_at": "2026-07-24T07:30:00+00:00",
      "code": null,
      "error": null
    },
    {
      "object_key": "references/image/def-456-ghi",
      "url": null,
      "expires_at": null,
      "code": "NOT_OWNED",
      "error": "Object key is not owned by the current principal."
    }
  ],
  "succeeded": 1,
  "failed": 1
}
```

---

## 13. seedream_edit_image

Edit an image interactively through ModelArk Seedream with point-based or
bounding-box targeting. The handler constructs `<point>` / `<bbox>` coordinate
markup from validated coordinates and prepends it to the instruction. At least
one reference image and one coordinate (point or bbox) are required.

### Input

| Field | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `prompt` | string | Yes | — | 1-4000 chars; coordinate markup is prepended automatically |
| `images` | list[MediaSource] | Yes | — | At least 1; max per model capabilities |
| `point` | EditCoordinate | No* | — | `x`,`y` in 0-999 |
| `bbox` | EditBbox | No* | — | `x1`,`y1`,`x2`,`y2` in 0-999 |
| `model` | string | No | Pro model | Must be in capability registry |
| `size` | string | No | — | e.g. "1024x1024" |
| `seed` | integer | No | — | -1 = random, 0+ = fixed |
| `output_format` | `"png"` \| `"jpeg"` | No | — | Not supported by 4.x models |
| `response_format` | `"url"` \| `"b64_json"` | No | — | — |
| `watermark` | boolean | No | — | AIGC watermark |
| `prompt_optimization` | `"standard"` \| `"fast"` | No | `SEEDREAM_PROMPT_OPTIMIZATION_MODE` on 5.0 Pro | Default `standard` |
| `persist` | boolean | No | `true` | Persist as durable MCP resources |
| `output_path` | string | No | — | Local file or directory (ending `/`); stdio only, requires `persist=true` |
| `overwrite` | boolean | No | `false` | Replace an existing `output_path` file |

\* Provide at least one of `point` or `bbox`.

### Output

| Field | Type | Description |
|---|---|---|
| `provider` | `"byteplus-modelark"` | Fixed |
| `model` | string | Model used |
| `created_at` | string | ISO-8601 |
| `artifacts` | list[ArtifactRef] | Edited images |
| `item_errors` | list[SeedreamItemError] | Per-item failures |
| `usage` | SeedreamUsage | Token usage |

### Example

```json
// Input
{
  "prompt": "Replace the object with a crown",
  "images": [{ "kind": "url", "url": "https://.../frame.png", "mime_type": "image/png" }],
  "bbox": { "x1": 100, "y1": 100, "x2": 400, "y2": 400 }
}
```

---

## 14. seed_understand

Understand images and videos, or reason about a task, through the Seed 2.1
multimodal model. Deep thinking is always on (depth set by `reasoning_effort`),
and only the final answer is returned: the reasoning trace is discarded and
never returned, logged, or saved. The default model
is `dola-seed-2-1-turbo-260628` (Seed 2.1 Turbo); `dola-seed-evolving` (the
latest Seed-series Pro-tier model) is also supported as a recognized built-in
ID — set `SEED_UNDERSTANDING_DEFAULT_MODEL=dola-seed-evolving` to opt in; its
family auto-resolves to `pro` so no explicit `SEED_UNDERSTANDING_MODEL_FAMILY`
is required. Other custom model IDs can be registered via
`SEED_UNDERSTANDING_MODEL_BINDINGS`. Video
Base64 is not supported by the chat endpoint — upload local videos via
`media_upload` first to obtain an HTTPS URL.

### Execution

`seed_understand` declares `execution.taskSupport="required"`. A compatible
MCP client invokes it as a task, receives the server-generated task ID without
waiting for the ModelArk response, and polls for the final tool result. The
server recommends a two-second polling interval. Foreground invocation is
rejected immediately instead of waiting until the client timeout expires.
Clients must request a task TTL long enough for the expected analysis duration.

The chat request timeout is `SEED_UNDERSTANDING_TIMEOUT_MS` (default:
`BYTEPLUS_REQUEST_TIMEOUT_MS`). A timeout returns `TIMEOUT` with
`retryable=true` and `ambiguous_completion=false`, but is not retried
automatically; 429 and 5xx responses are retried.

### Input

| Field | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `prompt` | string | Yes | — | 1-32,000 chars |
| `images` | list[UnderstandingImageInput] | No | — | Max 32; URL or Base64 |
| `videos` | list[UnderstandingVideoInput] | No | — | Max 32; URL only |
| `system` | string | No | — | Max 32,000 chars |
| `model` | string | No | Configured Seed 2.1 model | Must be in capability registry |
| `reasoning_effort` | `"low"` \| `"medium"` \| `"high"` | No | `"medium"` | Depth of deep thinking; always sent |
| `response_format` | UnderstandingResponseFormat | No | — | JSON / JSON Schema, enforced provider-side |
| `json_retry` | integer | No | `0` | 0-2 extra billed attempts on a JSON parse/validation failure; skipped when `finish_reason="length"` |
| `save_to` | string | No | — | Absolute file path; stdio only, inside an output root; validated before billing |
| `overwrite` | boolean | No | `false` | Replace an existing `save_to` file with different content |
| `return_content` | `"full"` \| `"summary"` \| `"none"` | No | `"full"` | `summary` inlines the first 2,000 characters; `none` inlines nothing |
| `temperature` | float | No | — | 0.0-2.0 |
| `max_tokens` | integer | No | — | 1-32,768, including deep-thinking tokens |
| `top_p` | float | No | — | 0.0-1.0 |
| `repetition_penalty` | float | No | — | 0.0-2.0; Ark-only |
| `thinking` | boolean | No | — | Deprecated and ignored; `false` only logs a warning |

### UnderstandingResponseFormat

| Field | Type | Required | Constraints |
|---|---|---|---|
| `type` | `"text"` \| `"json_object"` \| `"json_schema"` | Yes | — |
| `json_schema` | object | When `type="json_schema"` | Must be omitted otherwise |
| `json_schema.name` | string | Yes | `^[A-Za-z0-9_-]{1,64}$` |
| `json_schema.schema` | object | Yes | Valid JSON Schema draft 2020-12, at most 64 KB serialized |
| `json_schema.description` | string | No | Max 2,000 chars |
| `json_schema.strict` | boolean | No | Default `true` |

`save_to` writes the parsed JSON pretty-printed when the answer parsed as JSON,
otherwise the UTF-8 text. Reasoning is never written. If the write fails after
billing, the call still succeeds without `saved_path` and returns the full
answer inline.

### Output

| Field | Type | Description |
|---|---|---|
| `provider` | `"byteplus-modelark"` | Fixed |
| `model` | string | Model used |
| `completion_id` | string \| null | Provider completion ID |
| `choices` | list[UnderstandingChoice] | Final answers; see below |
| `usage` | UnderstandingUsage | Token usage summed over all attempts |
| `attempts` | integer | 1 plus any `json_retry` attempts used |
| `saved_path` | string \| null | Absolute path written for `save_to` |
| `saved_bytes` | integer \| null | Size of the written file |
| `request_id` | string \| null | Provider request ID |

**UnderstandingChoice:**

| Field | Type | Description |
|---|---|---|
| `role` | `"assistant"` | Fixed |
| `content` | string | Final answer; truncated for `summary`, empty for `none` |
| `content_chars` | integer | Full answer length before truncation |
| `content_truncated` | boolean | `true` when `content` is shorter than the full answer |
| `parsed` | object \| array \| null | Parsed JSON when a JSON `response_format` parsed (and, for `json_schema`, validated); returned even with `summary`/`none` |
| `schema_violation` | SchemaViolation \| null | `path` (JSON Pointer, `""` when not JSON), `message`, `finish_reason` |
| `finish_reason` | string | Provider finish reason; `length` means cut off by `max_tokens` |

**UnderstandingUsage:** `prompt_tokens`, `completion_tokens` (including
reasoning), `total_tokens`, and `reasoning_tokens` (thinking token cost when
the provider reports it).

### Example

```json
// Input
{
  "prompt": "Describe what happens in this video and identify the objects in this image.",
  "videos": [{ "kind": "url", "url": "https://.../sample.mp4", "mime_type": "video/mp4" }],
  "images": [{ "kind": "url", "url": "https://.../frame.png", "mime_type": "image/png" }],
  "reasoning_effort": "medium",
  "max_tokens": 4096
}
```

---

## 17. seed_media_get_artifact

Retrieve persisted media by artifact ID. Returns the raw media bytes
(Base64-encoded) with MIME type, SHA-256 hash, and byte count. Use this to
fetch media after the provider URL has expired (2h audio, 24h image/video).
Always registered; requires `artifacts:read` in JWT mode.

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `artifact_id` | string | Yes | Artifact ID returned by a previous generation call |

### Output

| Field | Type | Description |
|---|---|---|
| `artifact_id` | string | Artifact identifier |
| `media_type` | `"image"` \| `"audio"` \| `"video"` | Logical media type |
| `mime_type` | string | MIME type of the stored content |
| `sha256` | string | SHA-256 hex digest |
| `bytes` | integer | Size in bytes |
| `expires_at` | string \| null | Local artifact expiry, if applicable |
| `data` | string | Base64-encoded media data |

### Example

```json
// Input
{ "artifact_id": "71e9c2a8-..." }

// Output
{
  "artifact_id": "71e9c2a8-...",
  "media_type": "video",
  "mime_type": "video/mp4",
  "sha256": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
  "bytes": 1748096,
  "data": "AAAAIGZ0cBAAA..."
}
```

---

## 18. seed_media_export_artifact

Locate or copy a persisted media artifact on the local filesystem. Returns the
absolute on-disk path instead of streaming Base64 through the MCP context.
Stdio transport only — the client and server must share a filesystem. Always
registered; requires `artifacts:read` in JWT mode.

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `artifact_id` | string | Yes | Artifact ID returned by a previous generation call |
| `destination_path` | string | No | Absolute file path inside an allowed output root where the server writes an atomic copy; omit to return the canonical store path |
| `overwrite` | boolean | No | Replace an existing destination file with different content (default `false`) |

`destination_path` follows the [Local Output Paths](#local-output-paths) policy.
It must be absolute and inside a client MCP root or `OUTPUT_ROOTS`; an existing
file is never silently overwritten (identical content is accepted).

### Output

| Field | Type | Description |
|---|---|---|
| `artifact_id` | string | Artifact identifier |
| `path` | string | Absolute on-disk path of the exported media file |
| `media_type` | `"image"` \| `"audio"` \| `"video"` \| `"three_d"` | Logical media type |
| `mime_type` | string | MIME type of the stored content |
| `bytes` | integer | Size in bytes |
| `sha256` | string \| null | SHA-256 hex digest |
| `copied` | boolean | `true` when copied to `destination_path` (or an identical copy was already there); `false` when `path` is the canonical store location |

### Example

```json
// Input
{ "artifact_id": "71e9c2a8-..." }

// Output
{
  "artifact_id": "71e9c2a8-...",
  "path": "/abs/.artifacts/71/71e9c2a8-....mp4",
  "media_type": "video",
  "mime_type": "video/mp4",
  "bytes": 1748096,
  "sha256": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
  "copied": false
}
```

---

## 19. speech_to_text

Transcribe audio to text via Seed Speech ASR as a required background job. The
tool submits audio over HTTP and polls internally until complete; retrieve the
full `TranscriptionResult` from the background result. There is no separate
provider task tool or object-storage upload requirement.

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `audio` | AsrAudioInput | Yes | Audio source (see below) |
| `options` | AsrRequestOptions | No | Transcription feature toggles |

**AsrAudioInput** — provide exactly one source:

| Field | Type | Required | Description |
|---|---|---|---|
| `audio_url` | string | No* | HTTPS URL of the audio file (SSRF-safe, trusted hosts only) |
| `audio_data` | string | No* | Base64-encoded audio bytes |
| `audio_file_path` | string | No* | Absolute local file path (stdio transport only) |
| `audio_format` | `wav` \| `mp3` \| `ogg` \| `raw` \| `flac` | Yes | Audio format |

**AsrRequestOptions:**

| Field | Type | Default | Description |
|---|---|---|---|
| `language` | string | `"en-US"` | BCP-47 language code |
| `enable_punc` | boolean | `null` | Enable punctuation |
| `enable_itn` | boolean | `null` | Enable ITN |

### Output

| Field | Type | Description |
|---|---|---|
| `result` | TranscriptionResult | Full transcript: `text`, `utterances`, `duration_ms` |
| `log_id` | string \| null | Provider-side log ID |

### Example

```json
// Input
{
  "audio": { "audio_url": "https://example.com/meeting.wav", "audio_format": "wav" },
  "options": { "language": "en-US", "enable_punc": true, "enable_itn": true }
}
```

---

## 20. seed_media_persist_url

Persist a temporary provider output URL as a durable artifact. Use it to
recover an `ArtifactRef` with `id="provider-url"` and a `persistence_error`
before the provider URL expires. Always registered; requires `media:upload` in
JWT mode. Optional background task.

Only trusted BytePlus provider hosts are downloaded, through the same
SSRF-resistant downloader, host allowlist, redirect re-validation, and size
limits as generation-time persistence.

### Input

| Field | Type | Required | Constraints |
|---|---|---|---|
| `url` | string | Yes | HTTPS provider URL, 1-8192 chars |
| `media_type` | `"image"` \| `"audio"` \| `"video"` \| `"three_d"` | Yes | — |
| `mime_type` | string | Yes | Expected MIME type; a specific provider `Content-Type` takes precedence |
| `source_expires_at` | string | No | ISO-8601 URL expiry, copied from the original reference |

### Output

| Field | Type | Description |
|---|---|---|
| `artifact` | ArtifactRef | The new durable artifact |

A failure is a tool error naming the persistence code and whether it is
retryable.

---

## 21. media_upload_batch

Upload 1-50 media files to object storage in one call; each item gets its own
presigned HTTPS GET URL. Items are validated and uploaded with the same rules
as `media_upload` and fail independently. Registered with object storage;
requires `media:upload` in JWT mode. Required background task.

### Input

| Field | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `items` | list[MediaUploadBatchItemInput] | Yes | — | 1-50 items |
| `expires_in_seconds` | integer | No | configured TTL | 60–604800, applied to every item |
| `max_concurrent` | integer | No | `4` | 1-8 |

**MediaUploadBatchItemInput:**

| Field | Type | Required | Constraints |
|---|---|---|---|
| `media_type` | `"image"` \| `"audio"` \| `"video"` | Yes | — |
| `mime_type` | string | For `data` items | Inferred from the file extension for `file_path` items when omitted |
| `data` | string | one of | Base64 bytes; mutually exclusive with `file_path` |
| `file_path` | string | one of | Absolute local path (stdio only) |
| `key_prefix` | string | No | Default `"references"`; alphanumeric, `-`, `_`, `/` |

All items are validated and the total decoded size is compared with
`MEDIA_UPLOAD_BATCH_MAX_BYTES` (default 500 MiB) before any upload starts; an
oversized batch fails as a whole.

### Output

| Field | Type | Description |
|---|---|---|
| `items` | list[MediaUploadBatchItem] | Per-item results in request order |
| `succeeded` | integer | Items uploaded |
| `failed` | integer | Items that failed |
| `total_bytes` | integer | Bytes uploaded across successful items |

**MediaUploadBatchItem:** `index`, `file_path` (as given), `url`, `object_key`,
`expires_at`, `mime_type`, `bytes`, `error` (null on success), and `retryable`
(null on success).

---

## 22. seed_audio_understand

Understand, transcribe, translate, or reason about audio clips (speech, music,
or sound) through a ModelArk chat model with audio input. The model is set by
`SEED_AUDIO_UNDERSTANDING_MODEL` (default `seed-2-0-lite-260428`) and is not a
per-call argument. Deep thinking is always on (depth set by `reasoning_effort`),
and only the final answer is returned. For long-form transcription with
word-level timings, use `speech_to_text` instead.

The tool shares `seed_understand`'s answer-shaping fields and output shape:
`system`, `reasoning_effort`, `response_format`, `json_retry`, `save_to`,
`overwrite`, `return_content`, `temperature`, `max_tokens`, `top_p`, and
`repetition_penalty` behave exactly as documented in
[section 14](#14-seed_understand). It requires background execution and uses the
`understanding:read` scope and `SEED_UNDERSTANDING_TIMEOUT_MS`.

`json_schema` is enforced by the provider for `seed-2-0-lite-260428`;
`json_object` is not enforced by that model, so prefer `json_schema` (or set
`json_retry`) when you need JSON.

### Input

| Field | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `prompt` | string | Yes | — | 1-32,000 chars |
| `audios` | list[UnderstandingAudioInput] | Yes | — | 1-8 clips, sent in order |
| *(shared fields)* | — | No | — | As `seed_understand`, without `images`, `videos`, `model`, or `thinking` |

**UnderstandingAudioInput:** `kind` (`"url"` or `"base64"`), `url`, `data`,
`mime_type`.

- `kind="url"`: an HTTPS URL that passes the SSRF policy. The provider fetches
  and decodes it; `mime_type` is optional.
- `kind="base64"`: raw Base64 (no `data:` prefix), at most 10 MB decoded.
  `mime_type` is required and must be one of `audio/wav` (also `audio/x-wav`,
  `audio/wave`), `audio/mpeg` (`audio/mp3`), `audio/flac` (`audio/x-flac`),
  `audio/aac` (`audio/x-aac`), or `audio/mp4` (m4a). The provider rejects
  Base64 Ogg/Opus and raw PCM, so upload those via `media_upload` and pass the
  URL instead.

### Output

Same fields as [`seed_understand`](#14-seed_understand) (`provider`, `model`,
`completion_id`, `choices`, `usage`, `attempts`, `saved_path`, `saved_bytes`,
`request_id`). `model` is the configured `SEED_AUDIO_UNDERSTANDING_MODEL`.

### Example

```json
// Input
{
  "prompt": "Transcribe the speech and identify each speaker's emotion.",
  "audios": [{ "kind": "url", "url": "https://.../meeting.mp3" }],
  "reasoning_effort": "low",
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "speech_analysis",
      "schema": {
        "type": "object",
        "properties": {
          "transcript": { "type": "string" },
          "emotion": { "type": "string" }
        },
        "required": ["transcript", "emotion"],
        "additionalProperties": false
      }
    }
  }
}
```

---

## Operational HTTP endpoints

When Streamable HTTP is enabled, the ASGI application also exposes:

| Path | Purpose |
|---|---|
| `/health` | Process liveness |
| `/ready` | Runtime, SQLite state, and artifact-directory readiness |
| `/metrics` | Prometheus metrics exposition |

These are operational endpoints rather than MCP resources. Protect their
network reachability at the ingress or reverse-proxy layer.
