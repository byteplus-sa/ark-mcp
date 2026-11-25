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
| Poll Seedance tasks | [gen_task_get](/ark-mcp/tool-reference/gen-task-get/) | Batch status checks; lightweight calls can skip output persistence |
| List or cancel/delete tasks | [gen_task_list](/ark-mcp/tool-reference/gen-task-list/), [gen_task_cancel_or_delete](/ark-mcp/tool-reference/gen-task-cancel-or-delete/) | Owned-task lifecycle; cancellation/deletion has provider-specific semantics |

[Seedream 5.5](/ark-mcp/tool-reference/seedream-generate-image/#seedream-5-5) is the built-in image default
(`dola-seedream-5-5-261028`). It supports text-to-image, reference and point/bbox
editing, and transparent PNG generation without an already-transparent input.
Explicit operator defaults remain unchanged. Layer decomposition and native OCR
are not supported by the MCP integration.

Regular Seedance 2.5 is the default and supports `resolution="4k"` on the
same model, with no separate model or feature gate. Flash uses the same create
tool at 480p/720p without Draft. Empty bindings add Flash alongside the selected
default; explicit lists remain authoritative. Discovery does not prove account
entitlement. The models enabled on the ComfyUI node pack are not automatically
available in Ark MCP.

<span id="audio-and-understanding"></span>

## Audio

| Task | Tool | Credential |
| --- | --- | --- |
| Generate full-scene audio | [seed_audio_generate](/ark-mcp/tool-reference/seed-audio-generate/) | Seed Speech key |
| Generate speech from text or SSML | [seed_tts_generate](tools.md#seed_tts_generate) | Same Seed Speech key; required `voice_id` |
| Transcribe speech | [speech_to_text](/ark-mcp/tool-reference/speech-to-text/) | Seed Speech key |

Seed TTS 3.0 uses `dola-seed-tts-3.0` and is distinct from full-scene Seed Audio.
It requires background execution and always persists the full audio aggregated
from HTTP JSONL; it is not a live MCP audio stream. Discover it through service
`seed_tts` and `ark_list_models(kind="audio")`. See [Seed TTS 3.0](seed-tts.md)
for inputs, formats, billing and verification limits.

## Understanding

| Task | Tool | Credential |
| --- | --- | --- |
| Understand images/video | [seed_understand](/ark-mcp/tool-reference/seed-understand/) | ModelArk key |
| Understand/transcribe/translate audio | [seed_audio_understand](/ark-mcp/tool-reference/seed-audio-understand/) | ModelArk key |

Seed Audio Understanding is not the Seed Speech ASR service. It uses a
server-configured ModelArk model; do not pass an audio clip to `seed_understand`.
Understanding and transcription are provider operations, not free setup tests.

## Avatars

| Task | Tool | Requirements |
| --- | --- | --- |
| Image + audio to avatar video | [omnihuman_create_task](/ark-mcp/tool-reference/omnihuman-create-task/) | Vision AI key and `BYTEPLUS_OMNIHUMAN_ENABLED=true` |
| Poll/cancel avatars | [omnihuman_get_task](/ark-mcp/tool-reference/omnihuman-get-task/), [omnihuman_cancel_task](/ark-mcp/tool-reference/omnihuman-cancel-task/) | Vision AI credentials and gate |

OmniHuman has its own poller and cancel tool. Avatars are disabled by default.

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

## Moderation Lookup

Use [ark_moderation_get](/ark-mcp/tool-reference/ark-moderation-get/) to retrieve
reasons for an existing blocked provider record, not to screen new content.
Requires the default-off `BYTEPLUS_MODELARK_MODERATION_ENABLED` flag + ModelArk
AK/SK and account entitlement. Account-wide trusted stdio/local-mode loopback
only; `moderation:read` is excluded from shared-key HTTP. Call directly in the
foreground, not via `ark_job_submit` or native tasks.

Ten lookups per rolling minute per runtime; no automatic retries or result
persistence. Empty/not-found never means approval. Live success remains
unverified; see [Moderation Lookup](moderation.md) for eligibility and bounds.
