---
title: BytePlus VOD AI MediaKit Provider Contract
status: proposed
horizon: current
created: 2026-08-12
updated: 2026-10-09
tags:
  - byteplus-vod
  - ai-mediakit
  - provider-contract
source:
  - https://docs.byteplus.com/en/docs/byteplus-vod/docs-image-enhancement-template
  - https://docs.byteplus.com/en/docs/byteplus-vod/ai-mediakit-create-a-video-enhancement-task
  - https://docs.byteplus.com/en/docs/byteplus-vod/ai-mediakit-create-a-video-enhancement-lite-task
  - https://docs.byteplus.com/en/docs/byteplus-vod/reference-startexecution
  - https://docs.byteplus.com/en/docs/byteplus-vod/reference-getexecution
  - https://docs.byteplus.com/zh-CN/docs/byteplus-vod/ai-mediakit-create-a-video-transcoding-task
  - https://docs.byteplus.com/en/docs/byteplus-vod/ai-mediakit-get-task-details
  - https://docs.byteplus.com/en/docs/byteplus-vod/ai-mediakit-subtitle-burn-in
  - https://docs.byteplus.com/en/docs/byteplus-vod/ai-mediakit-create-a-subtitle-burn-in-task
  - https://docs.byteplus.com/en/docs/byteplus-vod/ai-mediakit-precision-erasure
  - https://docs.byteplus.com/en/docs/byteplus-vod/ai-mediakit-create-a-precision-erasure-task
related:
  - specs/SPEC_VOD_OPENAPI_PROVIDER_CONTRACT.md
  - docs/tools.md
---

<!-- markdownlint-disable MD013 MD025 -->

# BytePlus VOD AI MediaKit Provider Contract

## Scope and Verification Status

This specification governs the Bearer-authenticated convenience endpoints supplied
for MCP integration on the AI MediaKit data plane:

- `POST /api/v1/tools/enhance-video` (standard and professional tiers) and
  `POST /api/v1/tools/enhance-video-fast` (fast tier) +
  `GET /api/v1/tasks/{task_id}` (video enhancement, `vod_enhance_video` /
  `vod_get_enhancement_task`);
- `POST /api/v1/tools/transcode-video` + `GET /api/v1/tasks/{task_id}` (video
  transcoding, `vod_transcode_video` / `vod_get_transcode_task`);
- `POST /api/v1/tools/separate-voice` + `GET /api/v1/tasks/{task_id}` (voice
  and background audio separation, `vod_separate_audio` /
  `vod_get_audio_separation`);
- `POST /api/v1/tools/add-subtitle-to-video` + `GET /api/v1/tasks/{task_id}`
  (subtitle burn-in, `vod_add_subtitles` /
  `vod_get_subtitle_addition_task`);
- `POST /api/v1/tools/erase-video-subtitle-pro` + `GET /api/v1/tasks/{task_id}`
  (precision subtitle/text erasure, `vod_remove_subtitles` /
  `vod_get_subtitle_removal_task`).

The AK/SK-signed BytePlus VOD `StartExecution`/`GetExecution` surface is no
longer used by this server; its former contract is retained in the deprecated
`specs/SPEC_VOD_OPENAPI_PROVIDER_CONTRACT.md`.

### Video enhancement surface

Verification is **complete for submission and task retrieval** as of 2026-09-08:

- the endpoint route, method, request body, Bearer authentication, unauthenticated
  error envelope, and `x-tt-logid` response header are directly verified;
- approved credentialed probes directly verified that successful submission is
  asynchronous and returns top-level `task_id` and `request_id` fields;
- a read-only credentialed probe on 2026-09-08 confirmed that the generic
  `GET /api/v1/tasks/{task_id}` route accepts an enhancement task ID and returns
  `task_type="enhance-video"`, lifecycle status, result metadata, and a 24-hour
  output URL on completion;
- the completed output used the already trusted `*.byteplusvod.com` hostname and
  was persisted through the existing SSRF-safe artifact path;
- the MCP exposes the exact supplied request profile and a dedicated poll tool,
  does not retry ambiguous POST failures, and rejects unknown task types, states,
  and success shapes.

### Video transcoding surface

Verification is **complete for the request and task contracts** as of 2026-08-14
from the official AI MediaKit API reference (rendered via the zh-CN/zh-TW
variants, which carry identical English content, cross-confirmed on two
independent pages):

- the transcode request body (top-level and `video`/`audio` object field names and
  enum values) is documented and confirmed (see [Video Transcoding Request
  Contract](#video-transcoding-request-contract));
- the task polling endpoint `GET /api/v1/tasks/{task_id}` and its status/result/error
  shapes are documented and confirmed (see [Video Transcoding Task Contract](#video-transcoding-task-contract));
- the output URL lifetime (24 hours), idempotency (`client_token` plus a default
  24-hour deduplication key), and callback model are documented;
- the **output URL hostname and every redirect hop** were verified by a sanctioned
  live probe on 2026-08-14: the output host is
  `*.vod.ap-southeast-1.byteplusvod.com` (`.byteplusvod.com` suffix), which has
  been added to the artifact store's trusted-host policy. Durable persistence now
  works for confirmed outputs.
- `queue_id` and `Project` as *request* parameters are **unverified** and must not
  be exposed to clients.

### Subtitle burn-in and precision-erasure surfaces

Verification is **complete from the official API reference** as of 2026-09-09
for endpoint routes, Bearer authentication, request fields, asynchronous task
types, lifecycle states, completed MP4 result fields, and 24-hour output URL
lifetime. The supplied convenience-endpoint samples additionally establish
optional legacy `Project` and precision-erasure `model_version` fields; these
extensions are omitted by default and are not treated as required by the
current official contract.

Subtitle burn-in accepts a public video URL and at least one of a public
SRT/VTT/ASS `subtitle_url` or an inline `subtitles` cue list. A file URL takes
priority when both are present. Inline cues carry `subtitle_text`,
`start_time`, and `end_time`; the MCP rejects blank text and invalid time
ranges. Styling includes a position preset, positive pixel font size, RGBA
color, and a documented font identifier.

Precision erasure accepts `mode=Subtitle|Text` and
`output_encode_mode=Quality|Size`. The MCP additionally exposes documented
normalized erasure rectangles, selected/skipped time segments, and subtitle
OCR thresholds. Text mode is intentionally described as broader because it can
remove titles, labels, and watermarks in addition to dialogue subtitles.

Both submissions accept the documented `client_token`, callback, and queue
fields. Mutation POSTs are not automatically retried because completion is
ambiguous after a transport failure. Polling requires the exact task types
`add-subtitle-to-video` or `erase-video-subtitle-pro`, rejects a mismatched
echoed task ID or unknown state, and maps `running`/`processing`, `completed`,
and `failed` to the normalized task lifecycle. Completed outputs preserve the
provider URL independently of optional durable artifact persistence.
An HTTP 2xx envelope with `success: false` is a definitive provider rejection:
the adapter preserves its safe error code and request ID and does not mark the
submission as ambiguous. Malformed or unsupported 2xx mutation responses remain
ambiguous because dispatch may already have occurred.

## Authentication and Endpoint

```http
POST /api/v1/tools/enhance-video HTTP/2
Host: mediakit.ap-southeast-1.bytepluses.com
Authorization: Bearer ${BYTEPLUS_VOD_MEDIAKIT_API_KEY}
Content-Type: application/json
Accept: application/json
```

The credential is process configuration only. It must never be accepted as a tool
argument, logged, included in fixtures, or committed.

## Request Contract

The tool exposes the parameters documented in the official "Create a video quality
enhancement task" and "Create a video quality enhancement (fast) task" API
references (read 2026-10-09). `tool_version` selects the endpoint:
`standard`/`professional` post to `/tools/enhance-video`; `fast` posts to
`/tools/enhance-video-fast`.

```json
{
  "video_url": "https://media.example.com/source.mp4",
  "scene": "common",
  "tool_version": "professional",
  "resolution": "4k",
  "bitrate_level": "high",
  "fps": 24,
  "Project": "default"
}
```

| Field | MCP field | Type | Constraint |
| --- | --- | --- | --- |
| `video_url` | `video_url` | HTTPS URL | Public provider-fetchable video URL; no embedded credentials or local/private/link-local destination. |
| `tool_version` | `tool_version` | string | `fast`, `standard`, or `professional`; MCP default `professional`. `fast` is routed to its own endpoint and the field is not sent. |
| `scene` | `scene` | string | `common`, `ugc`, `short_series`, `aigc`, `old_film`; default `common`. Provider applies it only to `standard`; not sent for `fast`. |
| `enhance_style` | `enhance_style` | string | `hd` or `natural` for `standard`/`professional`; rejected for `fast`. Omitted unless set. |
| `resolution` | `resolution` | string | `240p`, `360p`, `480p`, `540p`, `720p`, `1080p`, `2k`, `4k`, `6k`, `8k` (`fast`: `240p`-`4k`). Mutually exclusive with `resolution_limit`; the MCP defaults to `4k` when neither is set. |
| `resolution_limit` | `resolution_limit` | integer | Short-side pixels, 128-4320 (`fast`: 128-2160), aspect ratio preserved. |
| `bitrate_level` | `bitrate_level` | string | `low`, `medium`, `high`; MCP default `high`. |
| `bitrate` | `bitrate` | integer | kbps, 10-150000; takes precedence over `bitrate_level`. |
| `fps` | `fps` | number | 15-120 (frame interpolation above the source rate; at most 4x the source is recommended). MCP default 24; `null` omits the field so the source rate is kept. |
| `Project` | `project` | string | Non-empty, maximum 128 characters; serialized with case-sensitive alias `Project`. Not sent for `fast`. |

Documented but intentionally not exposed: `bit_depth` and `codec` (professional
only; 10/12/16-bit and ProRes/FFV1/EXR outputs use non-MP4 containers that the
artifact persistence path does not yet handle), `client_token`, `callback_url`,
`callback_args`, and `queue_id`.

The fast-tier task result carries no `tool_version`; the adapter reports `fast`
when the polled `task_type` is `enhance-video-fast`. **Unverified:** the
task-details reference documents only `task_type="enhance-video"`. The
`enhance-video-fast` value is inferred from the fast task ID prefix
(`amk-tool-enhance-video-fast-*`) and must be confirmed by a credentialed probe.
Result `fps` is a number and may be fractional.

## Verified Error Contract

An unauthenticated empty JSON request returns HTTP 401, JSON content type, and:

```json
{
  "success": false,
  "error": {
    "code": "AuthenticationError",
    "type": "Unauthorized",
    "message": "The API key in the request is missing or invalid. Set valid api-key to proceed."
  }
}
```

The response includes `x-tt-logid`, which is the preferred diagnostic request ID.
Error parsing accepts this verified envelope and safely falls back to the HTTP status
when a different error body is returned. Provider response bodies and source URLs
must not be logged.

## Verified Asynchronous Acceptance Contract

An approved credentialed probe returned HTTP 200, JSON content type,
`x-tt-logid`, and the following sanitized shape:

```json
{
  "success": true,
  "task_id": "amk-tool-enhance-video-sanitized",
  "request_id": "request-id-sanitized"
}
```

The adapter returns this as `status="accepted"`, stores the task under the
provider-scoped ownership key `vod-mediakit`, and sets persistence to
`not_applicable`. The body `request_id` and header `x-tt-logid` are preserved
separately as `request_id` and `provider_log_id`.

This acceptance shape is specific to the enhancement surface. The generic
Bearer-authenticated AI MediaKit task endpoint is now live-confirmed for
enhancement; the documented AK/SK-signed VOD direct-edit progress/result APIs
remain a different contract and must not be substituted.

## Video Enhancement Task Contract

`GET /api/v1/tasks/{task_id}` with Bearer auth polls an enhancement task. A
sanitized completed response directly observed on 2026-09-08 had this shape:

```json
{
  "success": true,
  "task_id": "amk-tool-enhance-video-sanitized",
  "task_type": "enhance-video",
  "status": "completed",
  "result": {
    "tool_version": "professional",
    "video_url": "https://sanitized.vod.ap-southeast-1.byteplusvod.com/output.mp4",
    "duration": 30.917,
    "fps": 24,
    "resolution": "4k"
  },
  "expires_at": 1788962055,
  "created_at": 1788874787,
  "finished_at": 1788875656,
  "request_id": "sanitized",
  "queue_id": "default"
}
```

The adapter requires `task_type` to be `enhance-video` or `enhance-video-fast`, maps `running` to
`processing`, `completed` to `succeeded`, and `failed` to `failed`, and fails
closed for unknown values. A completed task must include `result.video_url`.
Epoch timestamps are normalized to ISO-8601 UTC. The output URL expires 24 hours
after completion in the observed contract.

## Provisional Completed-Result Compatibility Boundary

If BytePlus later returns a completed result directly, the adapter provisionally
supports a JSON success envelope with `success: true` and an object
under `data` or `result`. That object must contain one HTTPS output URL under
`output_url`, `video_url`, or `url`. These aliases are a narrow compatibility
boundary, not a claim that all are provider-documented.

The adapter may preserve these optional fields when present:

- `request_id` (body fallback when `x-tt-logid` is absent);
- `task_id` or `id`;
- `status`;
- `mime_type` or `content_type`;
- `expires_at` or `expiration` as an ISO-8601 string;
- `output_size_bytes` or `size` as a non-negative integer;
- terminal `error.code` and `error.message`.

Unknown or malformed HTTP 2xx bodies raise a normalized, non-retryable
`INVALID_RESPONSE` provider error. These aliases apply only when the submission
endpoint directly returns a completed result; asynchronous task IDs use the
separately verified enhancement task contract above.

## Execution and Retry Semantics (video enhancement)

- Submission is verified asynchronous. Task polling is verified through
  `vod_get_enhancement_task`; cancellation, list, and callback endpoints remain
  unverified and are not exposed.
- POST timeout, connection loss after dispatch, and HTTP 5xx are treated as
  ambiguous completion. They are not retried automatically.
- HTTP 429 is retryable only as provider guidance for a new user-initiated attempt;
  the mutation is not automatically replayed.
- Other 4xx responses are non-retryable until corrected.

## Output and Persistence Contract (video enhancement)

- An accepted response returns its task ID with `persistence="not_applicable"`;
  the task ID is then passed to `vod_get_enhancement_task`.
- A parsed completed response always returns its provider `source_url` to the
  authorized caller, even if local persistence is skipped or fails.
- The URL path/query is never logged. `source_expires_at` is returned only when the
  provider supplies it; the server does not invent a lifetime.
- Every initial output hostname and redirect hop must pass URL/IP validation and the
  repository's trusted-host policy. The observed output used the verified
  `*.byteplusvod.com` suffix.
- Durable video persistence is best-effort and capped at 209,715,200 bytes by the
  current artifact policy. A larger result remains a provider success with
  `persistence="failed"` and `output_too_large`.
- Persistence failure never changes provider `status="succeeded"` into a provider
  error. It returns a structured safe issue and preserves request/task/source-expiry
  metadata.

## Video Transcoding Request Contract

`POST /api/v1/tools/transcode-video` with Bearer auth and `Content-Type:
application/json`. Submission is asynchronous; a successful submission returns a
top-level `task_id` for polling.

### Top-level request fields

| Field | Type | Required | Allowed values / notes |
| --- | --- | --- | --- |
| `video_url` | String | Yes | Public HTTP/HTTPS URL. Source formats: mp4, flv, ts, avi, mov, wmv, mkv. |
| `container_format` | String | Yes | `MP4` (default), `FLV`, `MPEGTS`. |
| `video` | Object | Yes | See below. |
| `audio` | Object | No | Omitted → audio transcoded with `aac`, other params matching source. |
| `metadata_keep_tags` | Array[String] | No | e.g. `["title","artist"]`; no effect when `container_format=MPEGTS`. |
| `metadata_add_tags` | Array[Object] | No | `[{"key":"album","value":"2024 Remaster"}]`; no effect for MPEGTS. |
| `client_token` | String | No | Idempotency token; case-sensitive, ≤64 printable ASCII. |
| `callback_args` | String | No | Custom args ≤512 bytes, returned as-is in the completion event. |
| `callback_url` | String | No | Task-scoped callback endpoint; must be a public http(s) URL. |

### `video` object fields

| Field | Type | Default | Allowed values / constraints |
| --- | --- | --- | --- |
| `codec` | String | `h264` | `h264`, `h265`. |
| `scale_type` | Integer | `0` | `0` = follow-source (no scaling; scale fields ignored); `1` = long/short-side limit (activates `scale_short`/`scale_long`); `2` = width/height limit (activates `scale_width`/`scale_height`). |
| `scale_mode` | Integer | `0` | Effective when `scale_type` ∈ {1,2}: `0` = no upsampling (shrink only); `1` = stretch to target W×H; `2` = letterbox (scale to fit, black-bar padding). |
| `scale_width` | Integer | — | [0, 4320]; only when `scale_type=2`; if only width or height given, the other scales proportionally. |
| `scale_height` | Integer | — | [0, 4320]; only when `scale_type=2`. |
| `scale_short` | Integer | — | [0, 4320]; only when `scale_type=1`. |
| `scale_long` | Integer | — | [0, 4320]; only when `scale_type=1`. |
| `bitrate_mode` | String | `crf` | `crf` (Constant Rate Factor), `abr` (average bitrate), `cbr` (constant bitrate). |
| `bitrate_crf` | Number | `25` | [0, 51] (0 = lossless); only when `bitrate_mode=crf`. |
| `bitrate_kbps` | Integer | `2000` | [10, 50000]; crf = max limit / abr = average target / cbr = constant target. |
| `fps_mode` | String | `vfr` | `vfr` (fps = max limit, source kept if lower), `cfr` (forced constant); only takes effect after `fps` is set. |
| `fps` | Integer | — | [1, 240]; if unset, source frame rate is kept. |
| `is_hdr_to_sdr` | Boolean | `true` | `false` keeps HDR. |

### `audio` object fields (optional; not exposed in the initial MCP tool)

`codec` (only `aac`), `sample_rate` (default 44100; one of 8000/11025/12000/16000/
22050/24000/32000/44100/48000/64000/88200/96000), `bitrate_mode` (`cbr` default;
`cae` only when `audio.codec=aac`), `bitrate_kbps` ([10,500], default 128),
`channels` (1 mono / 2 stereo default), `volume_method` (`2Pass`),
`volume_integrated_loudness` (LUFS [-70,-5], default -12),
`volume_true_peak` (dBTP [-9,0], default 0), `volume_loudness_range` (LU [1,20], default 7).

### Submission response

Synchronous HTTP 200 acceptance:

```json
{
  "success": true,
  "task_id": "amk-tool-transcode-video-...",
  "request_id": "..."
}
```

On failure: `success=false`, `error: { "code": "InvalidParameter"|"MissingParameter"|"InternalServiceError", "type": "...", "message": "...", "param": "..." }`.

### Idempotency

`client_token` (≤64 printable ASCII, case-sensitive) forces a deduplication key.
Without it, the provider uses a default key of account + core request parameters
with a 24-hour window. A timed-out or ambiguous POST therefore *may* be safely
re-submitted with identical body/token to recover the original task, but the MCP
adapter still treats POST timeout/5xx as ambiguous and does not retry blindly.

## Video Transcoding Task Contract

`GET /api/v1/tasks/{task_id}` with Bearer auth polls a task.

### Documented status values

- `running` (also worded as "processing") — still executing;
- `completed` — success; the `result` object is populated;
- `failed` — terminal failure; the `error` object is populated.

`queued`, `expired`, and `cancelled` are **not documented** for AI MediaKit tasks
and must not be invented. The MCP adapter maps `running` → `processing`,
`completed` → `succeeded`, `failed` → `failed`; any other value fails closed as
`INVALID_RESPONSE`.

### Completed response

```json
{
  "success": true,
  "task_id": "amk-tool-transcode-video-112738623234",
  "task_type": "transcode-video",
  "status": "completed",
  "result": {
    "duration": 15.07,
    "resolution": "720p",
    "video_codec": "h264",
    "video_url": "https://example.com/transcoded_video.mp4?auth_key=..."
  },
  "expires_at": 1780472196,
  "created_at": 1780385775,
  "finished_at": 1780385797,
  "request_id": "...",
  "queue_id": "default"
}
```

- `result.video_url` is valid for **24 hours** by default.
- `expires_at` / `created_at` / `finished_at` are Unix-seconds timestamps (docs
  describe them as strings, examples show integers — the adapter accepts both and
  normalizes to ISO-8601 UTC for MCP output). The server does not invent a lifetime.
- `result.resolution` ∈ {`240p`, `360p`, `480p`, `540p`, `720p`, `1080p`, `2k`, `4k`, ...}.

### Failed-task error object

```json
{
  "code": "DownloadFailed",
  "message": "Failed to download file ...",
  "param": "video_url",
  "type": "TaskError"
}
```

`type` is `TaskError` for execution errors or `ApiError` for API errors. Error
messages are sanitized (URLs redacted) before being returned to clients.

### Transcode execution and retry semantics

- POST timeout / connection loss after dispatch / HTTP 5xx → ambiguous completion,
  never retried blindly (idempotency makes re-submission safe for the user, but the
  adapter does not auto-replay).
- GET polling errors: 429 is retryable (via `Retry-After`); other 4xx non-retryable;
  5xx on GET is treated as ambiguous by the gateway (consistent with POST) and is
  not retried automatically within `call_with_retry` — the client must re-poll.
- No cancellation or list endpoint is documented; no such tool is exposed.

### Transcode persistence contract

Same durable-persistence rules as enhancement: best-effort, capped at 209,715,200
bytes, per-redirect-hop validation, `source_url` always returned to the authorized
caller, persistence failures never erase provider success, and `source_url`
path/query never logged. The output hostname (`*.byteplusvod.com`) was confirmed
by a live probe on 2026-08-14 and added to the artifact store's trusted-host
policy; durable persistence works for confirmed outputs.

## Voice Separation Request Contract

`POST /api/v1/tools/separate-voice` with Bearer auth and `Content-Type:
application/json`. Submission is asynchronous; a successful submission returns a
top-level `task_id` for polling.

### Top-level request fields

| Field | Type | Required | Allowed values / notes |
| --- | --- | --- | --- |
| `audio_url` | String | Exactly one of `audio_url`/`video_url` | Public HTTPS URL. Audio formats: mp3, m4a, wav. |
| `video_url` | String | Exactly one of `audio_url`/`video_url` | Public HTTPS URL. Video formats: mp4, flv, ts, avi, mov, wmv, mkv. |
| `scene` | String | No (default `Audio`) | `Audio` (2-track voice + background), `Music` (2-track), `Drama` (3-track), `Narrate` (3-track). |
| `output_format` | String | No (default `aac`) | `aac`, `mp3`, `wav`, `m4a`, `flac`. |

### Submission response

```json
{
  "success": true,
  "task_id": "amk-tool-separate-voice-...",
  "request_id": "..."
}
```

### Task response

`GET /api/v1/tasks/{task_id}` reuses the transcode task lifecycle (`running` →
`processing`, `completed` → `succeeded`, `failed` → `failed`; anything else
fails closed as `INVALID_RESPONSE`).

Completed `result` tracks:

| Field | Presence | Notes |
| --- | --- | --- |
| `voice_audio_url` | 2-way and 3-way | Vocal track URL |
| `background_audio_url` | 2-way only | Background accompaniment URL |
| `music_audio_url` | 3-way only (`Drama`/`Narrate`) | Music track URL |
| `sfx_audio_url` | 3-way only (`Drama`/`Narrate`) | Sound-effects track URL |
| `duration` | optional | Float seconds |

A completed task with no track URLs fails closed as `INVALID_RESPONSE`.

### Separation execution, retry, and persistence

- POST timeout / connection loss / HTTP 5xx → ambiguous completion, never
  retried blindly.
- GET 429 → retryable (via `Retry-After`); other 4xx non-retryable; 5xx treated
  as ambiguous by the gateway.
- Output URLs are valid for 24 hours. The MCP tool preserves `source_url` and
  best-effort persists each track as a durable audio artifact under the
  `vod-mediakit` provider key, capped at the 10 MiB audio artifact limit.
- Track output hostnames follow the same trusted-host policy as transcode; the
  `*.byteplusvod.com` suffix is already allowlisted.

## Regional and Billing Boundaries

The endpoint hostname identifies `ap-southeast-1`, but this alone does not prove that
the convenience surface has the same availability or billing model as the current
VOD enhancement product. Official VOD documentation states that upgraded enhancement
is available in Johor and legacy enhancement remains in Singapore. Public VOD prices
must not be applied to this convenience endpoint unless BytePlus confirms the mapping;
the MCP returns no cost estimate while that mapping is unverified.

## Required Follow-Up Evidence

Before declaring this contract accepted or production-ready, obtain provider
documentation or sanitized evidence for:

- enhancement cancellation, list, callback, and idempotency behavior beyond the
  verified submit-and-poll lifecycle;
- redirect hostnames not exercised by the live enhancement result (the initial
  output host used confirmed `*.byteplusvod.com` and returned no redirect);
- provider-reported MIME, size metadata, and maximum output size (enhancement
  URL lifetime is confirmed at 24 hours by the live result);
- validation, quota, 429, and 5xx error envelopes;
- idempotency/reconciliation guarantees and pricing mapping (transcode idempotency:
  `client_token` + 24h default key confirmed);
- `queue_id` and `Project` as request parameters (unverified — the projects-and-queues
  guide is currently unreachable);
