---
title: Choose a Tool
description: Find the right Ark MCP capability and learn its credentials, execution mode and result lifecycle.
---

Ark MCP has one tool per capability. Models, variations, edits and previews
are parameters, not separate tool names. Start with `ark_list_models` and
`ark_job_capabilities` to discover your actual enabled surface.

The sidebar's **Tool Reference** contains one generated page per catalog tool,
with exact input/output schemas, nested types, registration requirements and
execution guidance. The tables below help you choose a starting point.

## Images and Video

| Task | Tool | What to know |
| --- | --- | --- |
| Generate or edit images | [seedream_generate_image](/ark-mcp/tool-reference/seedream-generate-image/) | ModelArk key; reference edits, point/bbox edits, variations and `dry_run` |
| Create video | [seedance_create_task](/ark-mcp/tool-reference/seedance-create-task/) | ModelArk key; select an enabled Seedance model, then poll the provider task |
| Poll video/3D tasks | [gen_task_get](/ark-mcp/tool-reference/gen-task-get/) | Batch status checks; lightweight calls can skip output persistence |
| List or cancel/delete tasks | [gen_task_list](/ark-mcp/tool-reference/gen-task-list/), [gen_task_cancel_or_delete](/ark-mcp/tool-reference/gen-task-cancel-or-delete/) | Owned-task lifecycle; cancellation/deletion has provider-specific semantics |

[Seedream 5.5](/ark-mcp/tool-reference/seedream-generate-image/#seedream-5-5) is the built-in image default
(`dola-seedream-5-5-261028`). It supports text-to-image, reference and point/bbox
editing, and transparent PNG generation without an already-transparent input.
Explicit operator defaults remain unchanged. Layer decomposition and native OCR
are not supported by the MCP integration.

Seedance 2.5 is the default generation. Premium 4K needs a feature gate and
provider whitelist; it is not a separate create tool. The models enabled on
the ComfyUI node pack are not automatically available in Ark MCP.

<span id="audio-and-understanding"></span>

## Audio

| Task | Tool | Credential |
| --- | --- | --- |
| Generate full-scene audio | [seed_audio_generate](/ark-mcp/tool-reference/seed-audio-generate/) | Seed Speech key |
| Transcribe speech | [speech_to_text](/ark-mcp/tool-reference/speech-to-text/) | Seed Speech key |

## Understanding

| Task | Tool | Credential |
| --- | --- | --- |
| Understand images/video | [seed_understand](/ark-mcp/tool-reference/seed-understand/) | ModelArk key |
| Understand/transcribe/translate audio | [seed_audio_understand](/ark-mcp/tool-reference/seed-audio-understand/) | ModelArk key |

Seed Audio Understanding is not the Seed Speech ASR service. It uses a
server-configured ModelArk model; do not pass an audio clip to `seed_understand`.
Understanding and transcription are provider operations, not free setup tests.

<span id="3d-and-avatars"></span>

## 3D

| Task | Tool | Requirements |
| --- | --- | --- |
| Text/image to 3D | [hyper3d_create_task](/ark-mcp/tool-reference/hyper3d-create-task/) | ModelArk key and `BYTEPLUS_MODELARK_3D_ENABLED=true` |
| Image to 3D | [hitem3d_create_task](/ark-mcp/tool-reference/hitem3d-create-task/) | Same 3D gate |

3D results use the shared `gen_task_*` lifecycle. The 3D feature is disabled
by default.

## Avatars

| Task | Tool | Requirements |
| --- | --- | --- |
| Image + audio to avatar video | [omnihuman_create_task](/ark-mcp/tool-reference/omnihuman-create-task/) | Vision AI key and `BYTEPLUS_OMNIHUMAN_ENABLED=true` |
| Poll/cancel avatars | [omnihuman_get_task](/ark-mcp/tool-reference/omnihuman-get-task/), [omnihuman_cancel_task](/ark-mcp/tool-reference/omnihuman-cancel-task/) | Vision AI credentials and gate |

OmniHuman has its own poller and cancel tool. Avatars are disabled by default.

## Media Processing

VOD AI MediaKit has a separate Bearer key,
`BYTEPLUS_VOD_MEDIAKIT_API_KEY`. Submit its operations in the background and
poll with `vod_get_task`, preserving the returned `operation`.

| Task | Tool |
| --- | --- |
| Enhance/upscale video | [vod_enhance_video](/ark-mcp/tool-reference/vod-enhance-video/) |
| Change codec, container, resolution or bitrate | [vod_transcode_video](/ark-mcp/tool-reference/vod-transcode-video/) |
| Burn subtitles | [vod_add_subtitles](/ark-mcp/tool-reference/vod-add-subtitles/) |
| Erase subtitles/on-screen text | [vod_remove_subtitles](/ark-mcp/tool-reference/vod-remove-subtitles/) |
| Separate voice and background audio | [vod_separate_audio](/ark-mcp/tool-reference/vod-separate-audio/) |
| Retrieve MediaKit task status/results | [vod_get_task](/ark-mcp/tool-reference/vod-get-task/) |

Do not use ModelArk `gen_task_get` to poll a VOD task.

## Uploads and Outputs

| Task | Tool |
| --- | --- |
| Upload local/Base64 references to TOS/S3 | [media_upload](/ark-mcp/tool-reference/media-upload/) |
| Renew object URLs | [media_presign](/ark-mcp/tool-reference/media-presign/) |
| Read stored artifacts inline | [seed_media_get_artifact](/ark-mcp/tool-reference/seed-media-get-artifact/) |
| Export stored artifacts to a local path | [seed_media_export_artifact](/ark-mcp/tool-reference/seed-media-export-artifact/) |
| Recover a provider URL after persistence failed | [seed_media_persist_url](/ark-mcp/tool-reference/seed-media-persist-url/) |

Video references need public HTTPS URLs. `media_upload` requires configured
object storage and accepts local paths only over stdio. Private-network URLs
are rejected. Store results promptly because provider links expire; consult
[Artifacts](artifacts.md) for durable resource access.

## Asset Library

The private asset library uses ModelArk IAM AK/SK, not a Bearer key:

- Group management: [ark_asset_group_ensure](/ark-mcp/tool-reference/ark-asset-group-ensure/) and [ark_asset_group_list](/ark-mcp/tool-reference/ark-asset-group-list/).
- Create/list/update assets: [ark_asset_create](/ark-mcp/tool-reference/ark-asset-create/), [ark_asset_list](/ark-mcp/tool-reference/ark-asset-list/), [ark_asset_update](/ark-mcp/tool-reference/ark-asset-update/).
- Real-person verification: [ark_asset_verification_start](/ark-mcp/tool-reference/ark-asset-verification-start/) and [ark_asset_verification_result](/ark-mcp/tool-reference/ark-asset-verification-result/).
- Delete: [ark_asset_delete](/ark-mcp/tool-reference/ark-asset-delete/), with an additional destructive-operation gate.

Use `asset://<id>` references in Seedance after the asset is usable.
See [Asset Library](assets.md) for rights, verification and lifecycle details.

## Background Jobs

Use these ordinary MCP tools on clients without native task augmentation:

| Tool | Purpose |
| --- | --- |
| [ark_job_capabilities](/ark-mcp/tool-reference/ark-job-capabilities/) | Discover enabled targets and their schemas |
| [ark_job_submit](/ark-mcp/tool-reference/ark-job-submit/) | Start a target operation and receive an Ark job ID |
| [ark_job_get](/ark-mcp/tool-reference/ark-job-get/) | Retrieve terminal original-tool results |
| [ark_job_cancel](/ark-mcp/tool-reference/ark-job-cancel/) | Cooperatively cancel local worker execution |

Keep job IDs distinct from provider task IDs. Honor polling hints, inspect
partial variation failures, and do not retry a potentially accepted create
call automatically. Start with [Your First Tool Call](setup/first-run.md).
