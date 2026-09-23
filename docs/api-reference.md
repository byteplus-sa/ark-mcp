# API Reference

Complete schemas, inputs, outputs, and examples for the conditional MCP tool
surface.

## Tool Inventory

| # | Tool | Product | Type | Auth |
|---|---|---|---|---|
| 1 | `seed_audio_generate` | Seed Audio | Required background task | Seed Speech |
| 2 | `seed_audio_generate_variations` | Seed Audio | Required background task | Seed Speech |
| 3 | `seedream_generate_image` | Seedream | Required background task | ModelArk |
| 4 | `seedream_generate_image_variations` | Seedream | Required background task | ModelArk |
| 5 | `seedance_create_task` | Seedance | Required background submission | ModelArk |
| 6 | `seedance_create_task_variations` | Seedance | Required background submission | ModelArk |
| 7 | `seedance_get_task` | Seedance | Optional background retrieval | ModelArk |
| 8 | `seedance_list_tasks` | Seedance | Read-only | ModelArk |
| 9 | `seedance_cancel_or_delete_task` | Seedance | Destructive | ModelArk |
| 10 | `media_upload` | Object storage (optional) | Required background task | TOS / S3 |
| 11 | `media_presign` | Object storage (optional) | Read-only | TOS / S3 |
| 12 | `media_presign_batch` | Object storage (optional) | Read-only | TOS / S3 |
| 13 | `seedream_edit_image` | Seedream | Required background task | ModelArk |
| 14 | `seed_understand` | Seed 2.1 (optional) | Required background task | ModelArk |
| 15 | `seedance_2_5_create_task` | Seedance 2.5 | Required background submission | ModelArk |
| 16 | `seedance_2_5_create_task_variations` | Seedance 2.5 | Required background submission | ModelArk |
| 17 | `seed_media_get_artifact` | Artifacts | Read-only | Local / JWT |
| 18 | `speech_to_text` | Seed Speech ASR (optional) | Required background task | Seed Speech |
| 19 | `vod_enhance_video` | VOD AI MediaKit (optional) | Required background submission | MediaKit Bearer |
| 20 | `vod_get_enhancement_task` | VOD AI MediaKit (optional) | Optional background retrieval | MediaKit Bearer |
| 21 | `vod_transcode_video` | VOD AI MediaKit (optional) | Required background submission | MediaKit Bearer |
| 22 | `vod_get_transcode_task` | VOD AI MediaKit (optional) | Optional background retrieval | MediaKit Bearer |
| 23 | `vod_separate_audio` | VOD AI MediaKit (optional) | Required background submission | MediaKit Bearer |
| 24 | `vod_get_audio_separation` | VOD AI MediaKit (optional) | Optional background retrieval | MediaKit Bearer |
| 25 | `vod_add_subtitles` | VOD AI MediaKit (optional) | Required background submission | MediaKit Bearer |
| 26 | `vod_get_subtitle_addition_task` | VOD AI MediaKit (optional) | Optional background retrieval | MediaKit Bearer |
| 27 | `vod_remove_subtitles` | VOD AI MediaKit (optional) | Required background submission | MediaKit Bearer |
| 28 | `vod_get_subtitle_removal_task` | VOD AI MediaKit (optional) | Optional background retrieval | MediaKit Bearer |
| 29 | `hyper3d_create_task` | Hyper3D (optional) | Required background submission | ModelArk |
| 30 | `hyper3d_get_task` | Hyper3D (optional) | Optional background retrieval | ModelArk |
| 31 | `hyper3d_list_tasks` | Hyper3D (optional) | Read-only | ModelArk |
| 32 | `hyper3d_cancel_or_delete_task` | Hyper3D (optional) | Destructive | ModelArk |
| 33 | `hitem3d_create_task` | Hitem3d (optional) | Required background submission | ModelArk |
| 34 | `hitem3d_get_task` | Hitem3d (optional) | Optional background retrieval | ModelArk |
| 35 | `hitem3d_list_tasks` | Hitem3d (optional) | Read-only | ModelArk |
| 36 | `hitem3d_cancel_or_delete_task` | Hitem3d (optional) | Destructive | ModelArk |
| 37 | `ark_job_capabilities` | Background jobs | Read-only ordinary tool | Local / JWT |
| 38 | `ark_job_submit` | Background jobs | Default background submission path | Dynamic target scope |
| 39 | `ark_job_get` | Background jobs | Read-only ordinary tool | Owner only |
| 40 | `ark_job_cancel` | Background jobs | Destructive ordinary tool | Owner only |
| 41 | `seed_media_export_artifact` | Artifacts | Locate (read-only) or copy (stdio only) | Local / JWT |
| 42 | `seed_media_persist_url` | Artifacts | Optional background task | Local / JWT |
| 43 | `media_upload_batch` | Object storage (optional) | Required background task | TOS / S3 |
| 44 | `seedance_get_tasks` | Seedance | Optional background retrieval | ModelArk |
| 45 | `seed_audio_understand` | Seed audio understanding (optional) | Required background task | ModelArk |

## Tool Annotations

| Tool | readOnly | destructive | idempotent | openWorld |
|---|---|---|---|---|
| `seed_audio_generate` | false | false | false | true |
| `seed_audio_generate_variations` | false | false | false | true |
| `seedream_generate_image` | false | false | false | true |
| `seedream_generate_image_variations` | false | false | false | true |
| `seedance_create_task` | false | false | false | true |
| `seedance_create_task_variations` | false | false | false | true |
| `seedance_get_task` | false | false | true | false |
| `seedance_list_tasks` | true | false | true | false |
| `seedance_cancel_or_delete_task` | false | true | false | true |
| `media_upload` | false | false | false | true |
| `media_presign` | true | false | true | false |
| `media_presign_batch` | true | false | true | false |
| `seedream_edit_image` | false | false | false | true |
| `seed_understand` | false | false | false | true |
| `seed_audio_understand` | false | false | false | true |
| `seedance_2_5_create_task` | false | false | false | true |
| `seedance_2_5_create_task_variations` | false | false | false | true |
| `seed_media_get_artifact` | true | false | true | false |
| `seed_media_export_artifact` | false | false | true | false |
| `speech_to_text` | true | false | true | false |
| `vod_enhance_video` | false | false | false | true |
| `vod_get_enhancement_task` | false | false | true | false |
| `vod_transcode_video` | false | false | false | true |
| `vod_get_transcode_task` | false | false | true | false |
| `vod_separate_audio` | false | false | false | true |
| `vod_get_audio_separation` | false | false | true | false |
| `vod_add_subtitles` | false | false | false | true |
| `vod_get_subtitle_addition_task` | false | false | true | false |
| `vod_remove_subtitles` | false | false | false | true |
| `vod_get_subtitle_removal_task` | false | false | true | false |
| `hyper3d_create_task` | false | false | false | true |
| `hyper3d_get_task` | false | false | true | false |
| `hyper3d_list_tasks` | true | false | true | false |
| `hyper3d_cancel_or_delete_task` | false | true | false | true |
| `hitem3d_create_task` | false | false | false | true |
| `hitem3d_get_task` | false | false | true | false |
| `hitem3d_list_tasks` | true | false | true | false |
| `hitem3d_cancel_or_delete_task` | false | true | false | true |
| `ark_job_capabilities` | true | false | true | false |
| `ark_job_submit` | false | false | false | true |
| `ark_job_get` | true | false | true | false |
| `ark_job_cancel` | false | true | false | true |
| `seed_media_persist_url` | false | false | false | true |
| `media_upload_batch` | false | false | false | true |
| `seedance_get_tasks` | false | false | true | false |

`seed_understand` and `seed_audio_understand` are not read-only because `save_to`
writes local files, and the
get-task tools (`seedance_get_task`, `seedance_get_tasks`, `hyper3d_get_task`,
`hitem3d_get_task`, and the `vod_get_*` tools) are not read-only because
`output_path`/`output_dir` write local files.

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

Seedance (`seedance_get_task`, `seedance_get_tasks`), Seed 3D, and MediaKit
get tools, and `seed_media_persist_url`, declare
`execution.taskSupport="optional"` because a processing-status check is short,
but a successful response with `persist_output=true` may download a large
artifact. Use foreground execution with persistence disabled for quick polling,
then background execution to retrieve and persist completed output. List,
presign, artifact-read, and cancel/delete tools remain foreground operations.

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
`seed_audio_generate`, `seedance_get_task`, `hyper3d_get_task`,
`hitem3d_get_task`, and the five MediaKit get tools; `output_dir` on
`seedream_generate_image_variations`, `seed_audio_generate_variations`, and
`seedance_get_tasks`; `save_to` on `seed_understand` and `seed_audio_understand`;
and `destination_path`
on `seed_media_export_artifact`. Each comes with an `overwrite` boolean
(default `false`).

| Rule | Behavior |
|---|---|
| Transport | stdio only; rejected on HTTP |
| Path form | Absolute. `output_path` names a file, or a directory when it ends with `/` (files named `<artifact_id>.<ext>`) |
| Allowed roots | Client MCP roots (`roots/list`) first, otherwise `OUTPUT_ROOTS`; with neither, path writing is disabled |
| Timing | Validated before any provider call |
| Preconditions | `persist=true` (generation tools) or `persist_output=true` (get tools); multi-image `seedream_generate_image` (`max_images > 1`) needs a directory |
| Existing files | Replaced only with `overwrite=true`; identical content is success |
| Failure | A failed local write never fails the call; see `ArtifactRef.local_path` / `export_error` |

For `seedance_get_task`, the last frame is written beside the video with a
`-last-frame` suffix (file target) or as its own `<artifact_id>.<ext>`
(directory target).

---

## vod_enhance_video

Enhance a public HTTPS video through the Bearer-authenticated BytePlus VOD AI
MediaKit convenience endpoint. The tool is registered only when
`BYTEPLUS_VOD_MEDIAKIT_API_KEY` is configured and requires the `vod:enhance`
scope in JWT mode.

The verified contract returns an accepted asynchronous task and deliberately fixes the
provider profile to `common` / `professional` / `4k` / `high` / 24 fps. Retrieve
the provider task ID from the background result, then poll it with
`vod_get_enhancement_task`. The POST is non-idempotent
and is never retried automatically because a timeout may occur after the provider
began processing.

### Input

| Field | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `video_url` | URL | Yes | — | Public HTTPS source; private, loopback, and link-local targets rejected |
| `scene` | `"common"` | No | `"common"` | Exact initial profile |
| `tool_version` | `"professional"` | No | `"professional"` | Exact initial profile |
| `resolution` | `"4k"` | No | `"4k"` | Exact initial profile |
| `bitrate_level` | `"high"` | No | `"high"` | Exact initial profile |
| `fps` | `24` | No | `24` | Frames per second |
| `project` | string | No | `"default"` | 1–128 characters; serialized upstream as `Project` |
| `input_duration_seconds` | number \| null | No | `null` | Positive; reserved for future pricing support |
| `persist` | boolean | No | `true` | Best-effort durable artifact copy |

### Output

Returns `VodEnhanceVideoOutput` with provider `byteplus-vod-mediakit` and
`status="accepted"` plus task/request IDs. If the provider directly returns a
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
  "scene": "common",
  "tool_version": "professional",
  "resolution": "4k",
  "bitrate_level": "high",
  "fps": 24,
  "project": "default",
  "persist": true
}
```

---

## vod_get_enhancement_task

Poll the status and retrieve the output of a BytePlus VOD AI MediaKit enhancement
task. Registered only when `BYTEPLUS_VOD_MEDIAKIT_API_KEY` is configured;
requires the `vod:read` scope in JWT mode.

### Input

| Field | Type | Required | Default |
|---|---|---|---|
| `task_id` | string | Yes | Provider task ID from the background result of `vod_enhance_video` |
| `persist_output` | boolean | No | `true` (requires background execution; use `false` for foreground status) |
| `output_path` | string | No | — (stdio only, inside an output root, requires `persist_output=true`; see [Local Output Paths](#local-output-paths)) |
| `overwrite` | boolean | No | `false` |

### Output

Returns `VodEnhancementTaskOutput` with normalized `processing`, `succeeded`, or
`failed` status. On success it includes the provider `source_url`, its 24-hour
expiry, duration, resolution, frame rate, enhancement tier, and task timestamps.
With persistence enabled, concurrent first polls share one artifact copy and
the `ArtifactRef` is cached for later reuse. Cache failures emit safe warnings
without discarding a created artifact. Artifact-copy failures are reported
separately without changing provider success.

### Example

```json
{ "task_id": "amk-tool-enhance-video-example" }
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
with a `task_id` for polling via `vod_get_transcode_task`. The non-idempotent
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
`status="accepted"`, task/request IDs, and a server-side heuristic
`recommended_poll_after_ms`.

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

## vod_get_transcode_task

Poll the status and output of a BytePlus VOD AI MediaKit transcode task.
Registered only when `BYTEPLUS_VOD_MEDIAKIT_API_KEY` is configured; requires the
`vod:read` scope in JWT mode.

### Input

| Field | Type | Required | Default |
|---|---|---|---|
| `task_id` | string | Yes | Provider task ID from the background result of `vod_transcode_video` |
| `persist_output` | boolean | No | `true` (requires background execution; use `false` for foreground status) |
| `output_path` | string | No | — (stdio only, inside an output root, requires `persist_output=true`; see [Local Output Paths](#local-output-paths)) |
| `overwrite` | boolean | No | `false` |

### Output

Returns `VodTranscodeTaskOutput` with `provider` `byteplus-vod-mediakit` and a
normalized `status` of `processing`, `succeeded`, or `failed` (the provider
documents only `running`/`completed`/`failed`). On success: `source_url`
(24-hour lifetime), optional `duration_seconds`/`resolution`/`video_codec`, and
normalized `created_at`/`finished_at`/`source_expires_at`. When
`persist_output=true` the completed output is copied once into the durable
artifact store (200 MiB cap) and cached by task ID; a persistence failure never
erases provider success. On failure, `error` carries the safe provider detail.

### Task Statuses

| Status | Meaning |
|---|---|
| `processing` | Provider reported `running`; still transcoding |
| `succeeded` | Provider reported `completed`; `source_url` available |
| `failed` | Provider reported `failed`; `error` populated |

### Example

```json
// Input
{ "task_id": "amk-tool-transcode-video-112738623234" }

// Output (succeeded)
{
  "provider": "byteplus-vod-mediakit",
  "task_id": "amk-tool-transcode-video-112738623234",
  "status": "succeeded",
  "provider_status": "completed",
  "duration_seconds": 15.07,
  "resolution": "720p",
  "video_codec": "h264",
  "source_url": "https://example.com/transcoded_video.mp4",
  "persistence": "persisted",
  "video": {
    "id": "71e9c2a8-...",
    "uri": "seed-media://artifacts/71e9c2a8-...",
    "media_type": "video",
    "mime_type": "video/mp4",
    "bytes": 1748096
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
and is omitted by default. The accepted output includes `task_id`, request IDs,
and a 5-second initial polling heuristic.

## vod_get_subtitle_addition_task

Poll `GET /tasks/{task_id}` with `vod:read`, using the provider task ID from the
background result of `vod_add_subtitles`. The adapter requires
`task_type="add-subtitle-to-video"`, validates the echoed provider task ID, and
maps provider state to `processing`, `succeeded`, or `failed`. On success it returns
the expiring `source_url`, duration/resolution when available, and optionally a
durable MP4 `video` artifact. `persist_output` defaults to true and requires
background execution; use `persist_output=false` for a foreground status
check. Persistence is single-flight and failure is reported separately from
provider success. `output_path` and `overwrite` write the persisted MP4 to a
[local path](#local-output-paths).

## vod_remove_subtitles

Submit `POST /tools/erase-video-subtitle-pro` using `vod:subtitle:remove`.
`mode="subtitle"` removes recognized dialogue subtitles; the broader
`mode="text"` may also remove titles, labels, or watermarks. Encoding mode is
`quality` or `size`. Optional precision controls include one-to-twenty
normalized erasure rectangles, selected/skipped time segments, and subtitle
OCR size/centering thresholds. Callback, queue, and `client_token` fields match
subtitle addition. The legacy `model_version` (`v4`/`v5`) and `project` fields
are omitted unless explicitly supplied. The output includes the task ID and a
15-second initial polling heuristic.

## vod_get_subtitle_removal_task

Poll `GET /tasks/{task_id}` with `vod:read`, using the provider task ID from the
background result of `vod_remove_subtitles`. The adapter requires
`task_type="erase-video-subtitle-pro"`; lifecycle normalization, task ownership,
source-URL preservation, best-effort single-flight MP4 persistence, and
`output_path`/`overwrite` match `vod_get_subtitle_addition_task`.

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
`status` `accepted`, the provider `request_id` and `provider_log_id`. Run it in
the background, then pass the provider task ID from its result to
`vod_get_audio_separation`. The mutation is never retried
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
  "recommended_poll_after_ms": 3000
}
```

---

## vod_get_audio_separation

Poll a BytePlus VOD AI MediaKit separate-voice task via `GET
/api/v1/tasks/{task_id}`. Registered when `BYTEPLUS_VOD_MEDIAKIT_API_KEY` is
configured and requires the `vod:read` scope in JWT mode.

| Field | Type | Required | Description |
|---|---|---|---|
| `task_id` | string | Yes | Provider task ID from the background result of `vod_separate_audio` |
| `persist_output` | boolean | No | Copy completed tracks into durable artifact storage on first successful poll (default `true`; requires background execution) |
| `output_path` | string | No | Local file or directory for the separated track; stdio only, requires `persist_output=true` |
| `overwrite` | boolean | No | Allow `output_path` to replace an existing file (default `false`) |

Returns `VodAudioSeparationTaskOutput` with a normalized `status` of
`processing`, `succeeded`, or `failed`. On success, `voice`, `background`,
`music`, and `sfx` each carry the track's expiring `source_url` (valid 24
hours) and, when best-effort persistence succeeds, a durable `artifact`
reference.

### Task Statuses

| Status | Meaning |
|---|---|
| `processing` | Provider reported `running`; still separating |
| `succeeded` | Provider reported `completed`; at least one track populated |
| `failed` | Provider reported `failed`; `error` populated |

### Example

```json
// Input
{ "task_id": "amk-tool-separate-voice-..." }

// Output (succeeded, 2-way)
{
  "provider": "byteplus-vod-mediakit",
  "task_id": "amk-tool-separate-voice-...",
  "status": "succeeded",
  "provider_status": "completed",
  "duration_seconds": 120.5,
  "voice": {
    "artifact": { "id": "...", "uri": "seed-media://artifacts/...", "media_type": "audio", "mime_type": "audio/aac", "bytes": 1787924 },
    "source_url": "https://vod.ap-southeast-1.byteplusvod.com/voice.aac?sign=...",
    "persistence": "persisted"
  },
  "background": {
    "artifact": { "id": "...", "uri": "seed-media://artifacts/...", "media_type": "audio", "mime_type": "audio/aac", "bytes": 1787924 },
    "source_url": "https://vod.ap-southeast-1.byteplusvod.com/background.aac?sign=...",
    "persistence": "persisted"
  }
}
```

---

## Shared Types

### MediaSource

A media reference by URL or Base64.

| Field | Type | Required | Description |
|---|---|---|---|
| `kind` | `"url"` \| `"base64"` | Yes | Source type |
| `url` | string | If kind=url | HTTPS URL |
| `data` | string | If kind=base64 | Base64-encoded data |
| `mime_type` | string | No | MIME type |

### AudioReference

An audio reference for voice cloning.

| Field | Type | Required | Description |
|---|---|---|---|
| `kind` | `"speaker"` \| `"url"` \| `"base64"` | Yes | Reference mode |
| `speaker_id` | string | If kind=speaker | Preset speaker ID |
| `url` | string | If kind=url | Reference audio URL |
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

Create an asynchronous Seedance video generation task.

### Input

| Field | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `prompt` | string | No | — | 1-32,000 chars |
| `images` | list[SeedanceImageInput] | No | — | Max 9 |
| `videos` | list[SeedanceVideoInput] | No | — | Max 3 |
| `audios` | list[SeedanceAudioInput] | No | — | Max 3 |
| `model` | string | No | Configured | — |
| `resolution` | `"480p"` \| `"720p"` \| `"1080p"` \| `"4k"` | No | — | Model-dependent |
| `ratio` | string | No | — | e.g. "16:9". For `extend_video`, stripped (auto-locks to source) to prevent `InvalidParameter.TaskTypeConstraint`. For `edit_video`, auto-derived from input video. For first/last-frame, locks to first image. |
| `duration` | integer | No | — | -1 (auto) or 4-15. Ignored for edit tasks (auto-derived from input video) |
| `omni_reference_task_type` | string | No | `auto` | Task type hint (e.g. `edit_video`, `extend_video`) |
| `generate_audio` | boolean | No | — | — |
| `watermark` | boolean | No | — | — |
| `return_last_frame` | boolean | No | — | — |
| `execution_expires_after` | integer | No | — | 3600-259200 seconds |
| `priority` | integer | No | — | 0-9 |
| `safety_identifier` | string | No | — | Max 64 chars |

Text-only input (prompt with no media) is supported for pure text-to-video
generation. Audio cannot be the sole media input — at least a prompt,
image, or video is required.

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

### SeedanceImageInput

Extends `MediaSource` with a `role` field:

| Field | Type | Description |
|---|---|---|
| `role` | `"first_frame"` \| `"last_frame"` \| `"reference_image"` | Image purpose |

### SeedanceVideoInput

Video references are URL-only. Unlike image and audio references, there is no
Base64 path — videos must be uploaded to a publicly reachable HTTPS endpoint
before the tool is called. Use the `media_upload` tool to upload Base64 or
a local file (stdio only) to object storage (TOS or S3) and receive a presigned
HTTPS GET URL. Alternatively, host the video on your own HTTPS endpoint.
The URL must resolve to a public IP (loopback, private, and link-local
addresses are rejected by the SSRF policy).

| Field | Type | Description |
|---|---|---|
| `kind` | `"url"` | Always `url` (hard-coded) |
| `url` | string | Public HTTPS URL of the reference video |
| `role` | `"reference_video"` | Always `reference_video` |

### Output

| Field | Type | Description |
|---|---|---|
| `task_id` | string | Task ID for polling |
| `status` | `"queued"` | Initial status |
| `recommended_poll_after_ms` | integer | Suggested poll delay |

### Example

```json
// Input
{
  "prompt": "A cat walking through a garden",
  "images": [
    { "kind": "url", "url": "https://...", "role": "reference_image" }
  ],
  "resolution": "480p",
  "duration": 5
}

// Output
{
  "task_id": "cgt-20260721134956-h5cz9",
  "status": "queued",
  "recommended_poll_after_ms": 5000
}
```

---

## 6. seedance_create_task_variations

Create N independent Seedance video tasks in parallel.

### Input

Inherits all fields from `seedance_create_task`, plus:

| Field | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `prompt` | string | No* | — | 1-32,000 chars |
| `variations` | integer | No | 1 | 1-5 |
| `variation_prompts` | list[string] | No | — | Must have `variations` entries; each 1-32,000 chars |

\* Either `prompt` or `variation_prompts` must be provided.

### Output

| Field | Type | Description |
|---|---|---|
| `summary` | VariationSummary | Per-variation task IDs |
| `recommended_poll_after_ms` | integer | Poll delay for all tasks |

### Example

```json
// Input
{
  "variation_prompts": [
    "The cat walks forward slowly",
    "The cat jumps playfully"
  ],
  "variations": 2,
  "images": [
    { "kind": "base64", "data": "...", "mime_type": "image/png", "role": "reference_image" }
  ],
  "resolution": "480p",
  "duration": 5
}

// Output
{
  "summary": {
    "total": 2,
    "succeeded": 2,
    "failed": 0,
    "variations": [
      { "index": 0, "task_id": "cgt-...-rq5gm" },
      { "index": 1, "task_id": "cgt-...-hj27l" }
    ]
  },
  "recommended_poll_after_ms": 5000
}
```

---

## 7. seedance_get_task

Retrieve the status and output of a Seedance task.

### Input

| Field | Type | Required | Default |
|---|---|---|---|
| `task_id` | string | Yes | Provider task ID from the background result of a Seedance create tool |
| `persist_output` | boolean | No | `true` (requires background execution; use `false` for foreground status) |
| `output_path` | string | No | — (video file or directory; last frame written beside it with `-last-frame` suffix; see [Local Output Paths](#local-output-paths)) |
| `overwrite` | boolean | No | `false` |

### Output

| Field | Type | Description |
|---|---|---|
| `task_id` | string | Task ID |
| `model` | string | Model used |
| `status` | SeedanceTaskStatus | Current status |
| `created_at` | string | ISO-8601 |
| `updated_at` | string | ISO-8601 |
| `error` | object \| null | Error details |
| `video` | ArtifactRef \| null | Persisted video (on success) |
| `last_frame` | ArtifactRef \| null | Persisted last frame |
| `usage` | SeedanceTaskUsage \| null | Token usage |
| `settings` | object | Generation settings |
| `queue` | SeedanceQueueInfo \| null | Server-derived queue timing while `queued`/`running`; `null` once finished |

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
| `succeeded` | Completed, video available |
| `failed` | Failed, check `error` |
| `expired` | Expired before completion |
| `cancelled` | Was cancelled |

### Example

```json
// Input
{ "task_id": "cgt-20260721134956-h5cz9" }

// Output (succeeded)
{
  "task_id": "cgt-...",
  "model": "dreamina-seedance-2-0-260128",
  "status": "succeeded",
  "created_at": "2026-07-21T06:02:19+00:00",
  "updated_at": "2026-07-21T06:06:13+00:00",
  "video": {
    "id": "71e9c2a8-...",
    "uri": "seed-media://artifacts/71e9c2a8-...",
    "media_type": "video",
    "mime_type": "video/mp4",
    "bytes": 1748096
  },
  "usage": { "completion_tokens": 48400 }
}
```

---

## 8. seedance_list_tasks

List recent Seedance tasks (last 7 days).

### Input

| Field | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `page` | integer | No | 1 | 1-500 |
| `page_size` | integer | No | 20 | 1-100 |
| `status` | SeedanceTaskStatus | No | — | Filter by status |
| `task_ids` | list[string] | No | — | Filter by IDs |
| `model` | string | No | — | Filter by model |
| `service_tier` | `"default"` \| `"flex"` | No | — | — |

### Output

| Field | Type | Description |
|---|---|---|
| `tasks` | list[SeedanceTaskSummary] | Task summaries, each with the same `queue` timing as `seedance_get_task` |
| `total` | integer | Total matching tasks |
| `page` | integer | Current page |
| `page_size` | integer | Page size |
| `has_more` | boolean | More pages available |

---

## 9. seedance_cancel_or_delete_task

Cancel (queued) or delete (terminal) a Seedance task.

### Input

| Field | Type | Required | Description |
|---|---|---|---|
| `task_id` | string | Yes | Task ID |
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

## 15. seedance_2_5_create_task

Create an asynchronous Seedance 2.5 video generation task. Supports up to
30-second video generation, 50 multimodal references (30 images, 10 videos,
10 audio), and 480p/720p/1080p resolution. Poll with `seedance_get_task`.

### Input

| Field | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `prompt` | string | No | — | 1-32,000 chars |
| `images` | list[SeedanceImageInput] | No | — | Max 30; roles `first_frame`, `last_frame`, `reference_image` |
| `videos` | list[SeedanceVideoInput] | No | — | Max 10; URL-only, role `reference_video` |
| `audios` | list[SeedanceAudioInput] | No | — | Max 10; audio-only input is supported |
| `model` | string | No | `dreamina-seedance-2-5-260628` | Must resolve to a Seedance 2.5 family |
| `resolution` | `"480p"` \| `"720p"` \| `"1080p"` | No | — | 4k not supported |
| `ratio` | string | No | — | e.g. "16:9". Stripped for `extend_video`; auto-derived for `edit_video`/first-frame |
| `duration` | integer | No | — | -1 (auto) or 1-30. Ignored for edit tasks |
| `omni_reference_task_type` | string | No | `auto` | Task type hint (e.g. `edit_video`, `extend_video`) |
| `generate_audio` | boolean | No | — | Generate an audio track |
| `watermark` | boolean | No | `false` | AIGC watermark |
| `return_last_frame` | boolean | No | — | Return last frame as image |
| `execution_expires_after` | integer | No | — | 3600-259200 seconds |
| `priority` | integer | No | — | 0-9 |
| `safety_identifier` | string | No | — | Max 64 chars |

### Output

| Field | Type | Description |
|---|---|---|
| `task_id` | string | Task ID for polling |
| `status` | `"queued"` | Initial status |
| `recommended_poll_after_ms` | integer | Suggested poll delay |

### Example

```json
// Input
{
  "prompt": "A cat walking through a garden",
  "duration": 10,
  "resolution": "1080p"
}

// Output
{
  "task_id": "cgt-20260721134956-h5cz9",
  "status": "queued",
  "recommended_poll_after_ms": 5000
}
```

---

## 16. seedance_2_5_create_task_variations

Create N independent Seedance 2.5 video generation tasks in parallel. Each
variation creates a separate provider task. Retrieve the background result,
then pass each provider task ID to `seedance_get_task`.
Partial failures are captured per variation.

### Input

Inherits all fields from `seedance_2_5_create_task`, plus:

| Field | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `prompt` | string | No* | — | Base prompt, 1-32,000 chars |
| `variations` | integer | No | 1 | 1-5 |
| `variation_prompts` | list[string] | No | — | Must have `variations` entries; each 1-32,000 chars |

\* Either `prompt` or `variation_prompts` must be provided.

### Output

| Field | Type | Description |
|---|---|---|
| `summary` | VariationSummary | Per-variation task IDs and errors |
| `recommended_poll_after_ms` | integer | Suggested poll delay |

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

## 22. seedance_get_tasks

Check 1-50 Seedance tasks in one call. Uses one provider list call per page of
20 task IDs (at most three), filtered by
task ID, and re-fetches individually only tasks missing from the list or
succeeded without an output URL. Requires `seedance:read` in JWT mode.
Optional background task (required when `persist_output=true`).

### Input

| Field | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `task_ids` | list[string] | Yes | — | 1-50; duplicates ignored |
| `persist_output` | boolean | No | `false` | Persist each succeeded task's video and last frame once |
| `output_dir` | string | No | — | Local directory; stdio only, requires `persist_output=true` |
| `overwrite` | boolean | No | `false` | Replace existing files in `output_dir` |

### Output

| Field | Type | Description |
|---|---|---|
| `tasks` | list[SeedanceTaskOutput] | Found tasks in request order, same shape as `seedance_get_task` |
| `errors` | list[SeedanceTaskLookupError] | `task_id`, `code` (`NOT_OWNED`, `NOT_FOUND`, or a provider code), `message` |
| `counts` | object | Number of found tasks per status |
| `all_terminal` | boolean | `true` when every found task has finished |
| `active_tasks` | integer | Found tasks still queued or running |

---

## 23. seed_audio_understand

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
