# Use Cases

The Ark Seed MCP server exposes a conditional multimodal tool surface.
Here are common scenarios and how to achieve them.

The JSON blocks below show tool input payloads. Wrap the payload for
generation, variation, transcription, upload, understanding, and
provider-submission tools in `ark_job_submit`, or invoke those tools with MCP
task augmentation on a client that negotiates it. For Seedance, Seed 3D, and
MediaKit
retrieval, poll in foreground with `persist_output=false`, then use the
`ark_job_*` path (or optional task augmentation) with `persist_output=true`
when downloading a completed output.

In the workflows below, **background result** means the original tool result
under terminal `ark_job_get.result`, or the terminal `tasks/get` response on
the native path.

## 1. Text-to-Image Generation

Generate a single image from a text prompt.

**Tool:** `seedream_generate_image`

```json
{
  "prompt": "A serene mountain landscape at sunset, digital art",
  "size": "1024x1024",
  "output_format": "jpeg",
  "persist": true
}
```

The tool returns a durable `ArtifactRef` that survives the 24-hour provider
URL expiry. The artifact is retrievable via
`seed-media://artifacts/{artifact_id}`.

## 2. Image Editing

Edit an existing image using a text prompt and reference image.

**Tool:** `seedream_generate_image`

```json
{
  "prompt": "Change the background to a beach scene while keeping the subject",
  "images": [
    {
      "kind": "url",
      "url": "https://cdn.example.com/original.png"
    }
  ],
  "size": "1024x1024",
  "persist": true
}
```

## 3. Reproducible Image Generation

Generate an image with a fixed seed for reproducibility.

**Tool:** `seedream_generate_image`

```json
{
  "prompt": "A cat sitting on a windowsill",
  "seed": 42,
  "size": "1024x1024"
}
```

Using the same seed and prompt produces the same image.

## 4. Parallel Image Variations

Generate multiple distinct image variations in a single call to give the
user choices. Each variation gets a distinct seed.

**Tool:** `seedream_generate_image_variations`

```json
{
  "prompt": "A futuristic city skyline, cyberpunk aesthetic",
  "variations": 5,
  "base_seed": 100,
  "size": "1024x1024",
  "persist": true
}
```

This produces 5 images with seeds [100, 101, 102, 103, 104]. Each is an
independent generation. Partial failures are captured — if 4 of 5 succeed,
the tool returns 4 artifacts and 1 error.

## 5. Per-Variation Prompts

Generate variations with different prompts for each.

**Tool:** `seedream_generate_image_variations`

```json
{
  "variation_prompts": [
    "A cat in spring, cherry blossoms",
    "A cat in summer, sunny garden",
    "A cat in autumn, fallen leaves",
    "A cat in winter, snow"
  ],
  "variations": 4,
  "persist": true
}
```

## 6. Audio Generation (Text-to-Speech)

Generate speech from a text prompt.

**Tool:** `seed_audio_generate`

```json
{
  "text_prompt": "Welcome to the Ark Seed Multimodal MCP Server.",
  "output": {
    "format": "wav",
    "sample_rate": 44100
  },
  "persist": true
}
```

## 7. Voice Cloning with References

Generate audio that mimics a reference voice.

**Tool:** `seed_audio_generate`

```json
{
  "text_prompt": "Hello, this is a cloned voice test.",
  "audio_references": [
    {
      "kind": "url",
      "url": "https://cdn.example.com/voice-sample.wav",
      "mime_type": "audio/wav"
    }
  ],
  "persist": true
}
```

## 8. Parallel Audio Variations

Generate multiple audio takes to choose from.

**Tool:** `seed_audio_generate_variations`

```json
{
  "text_prompt": "The quick brown fox jumps over the lazy dog.",
  "variations": 3,
  "persist": true
}
```

## 9. Video Generation (Text-to-Video)

Create an async video generation task.

**Tool:** `seedance_create_task`

```json
{
  "prompt": "A cat walking through a garden, warm sunlight",
  "images": [
    {
      "kind": "url",
      "url": "https://cdn.example.com/reference.png",
      "role": "reference_image"
    }
  ],
  "resolution": "480p",
  "duration": 5
}
```

Omitting `model` uses the configured default (Seedance 2.5); pass an exact ID
from `ark_list_models(kind='video')` to pick another model. Add
`"dry_run": true` to preview the request and estimated cost without billing.

The background submission returns a local MCP task or Ark job ID. Read the
provider task ID (`tasks[0].task_id`) from its terminal result, then pass that
provider ID to `gen_task_get`.

## 10. Polling for Video Completion

Check the status of one or more video (or 3D) generation tasks.

**Tool:** `gen_task_get`

```json
{
  "task_ids": ["provider-task-abc123"]
}
```

`persist_output` defaults to `false`, so this is a foreground status poll.
Wait `recommended_poll_after_ms` between polls and stop when `all_terminal` is
`true`; **never resubmit the create tool to poll**. To persist the completed
outputs, run the call as a background job with `persist_output=true` after the
task reaches a terminal state; subsequent polls return the cached artifact
without re-downloading.

## 11. First/Last Frame Video Generation

Generate a video that starts with one image and ends with another.

**Tool:** `seedance_create_task`

```json
{
  "prompt": "Flowers blooming from bud to full bloom",
  "images": [
    {
      "kind": "url",
      "url": "https://cdn.example.com/bud.png",
      "role": "first_frame"
    },
    {
      "kind": "url",
      "url": "https://cdn.example.com/full-bloom.png",
      "role": "last_frame"
    }
  ],
  "resolution": "720p",
  "duration": 5
}
```

## 12. Parallel Video Task Variations

Create multiple video tasks with different prompts in one call
(`variations` 1-5; each variation is a separate billed task).

**Tool:** `seedance_create_task`

```json
{
  "variation_prompts": [
    "The cat walks forward slowly through the garden",
    "The cat looks around curiously, then jumps playfully"
  ],
  "variations": 2,
  "images": [
    {
      "kind": "base64",
      "data": "base64-encoded-png-data",
      "mime_type": "image/png",
      "role": "reference_image"
    }
  ],
  "resolution": "480p",
  "duration": 5
}
```

The background submission returns a local MCP task or Ark job ID. Read the
per-variation provider task IDs from `tasks` in its terminal result (a failed
submission has `error` instead of `task_id`), then pass all of them to one
`gen_task_get` call.

## 13. List Recent Video Tasks

List your generation tasks from the last 7 days, newest first.

**Tool:** `gen_task_list`

```json
{
  "page_size": 20,
  "status": "succeeded"
}
```

Pass `next_cursor` back as `cursor` with the same filters until it is `null`.

## 14. Cancel or Delete a Video Task

Cancel a queued task or delete a completed one (Seedance or 3D).

**Tool:** `gen_task_cancel_or_delete`

Cancel (queued):

```json
{
  "task_id": "cgt-20260721134956-h5cz9",
  "mode": "cancel",
  "expected_status": "queued"
}
```

Delete (terminal):

```json
{
  "task_id": "cgt-20260721134956-h5cz9",
  "mode": "delete",
  "expected_status": "succeeded"
}
```

## 15. End-to-End: Image-to-Video Pipeline

Generate a reference image, then use it as input for video generation.

1. Generate an image in the background, then read its background result:

```
seedream_generate_image({
  "prompt": "A fluffy orange cat on a garden path",
  "size": "1024x1024",
  "response_format": "b64_json",
  "persist": true
})
```

2. Use the generated image (as base64) to create a video task in the
   background. Read its provider task ID from the background result:

```
seedance_create_task({
  "prompt": "The cat walks forward through the garden",
  "images": [{
    "kind": "base64",
    "data": "<base64 from step 1>",
    "mime_type": "image/png",
    "role": "reference_image"
  }],
  "resolution": "480p",
  "duration": 5
})
```

3. Poll for video completion in the foreground:

```
gen_task_get({"task_ids": ["<provider task ID from background result>"]})
```

## 16. Batch Storyboard with Seedream

Generate a coherent set of related images using the provider's native batch
mode (not parallel variations — these share visual continuity).

**Tool:** `seedream_generate_image`

```json
{
  "prompt": "A set of four seasons landscape illustrations: spring, summer, autumn, winter",
  "max_images": 4,
  "size": "1024x1024",
  "persist": true
}
```

This uses `sequential_image_generation: "auto"` — the provider generates a
coherent storyboard, not independent variations.

## 17. Reference Video Upload via Object Storage

Upload a video to object storage (TOS or S3), then use the presigned URL as
a video reference for Seedance. Requires TOS or S3 credentials.

**Step 1 — Upload:**

**Tool:** `media_upload`

```json
{
  "media_type": "video",
  "mime_type": "video/mp4",
  "data": "base64-encoded-video-bytes"
}
```

Returns:

```json
{
  "url": "https://bucket.tos-ap-southeast-1.bytepluses.com/references/video/abc?...",
  "expires_at": "2026-07-24T07:30:00+00:00",
  "object_key": "references/video/abc",
  "bytes": 5242880
}
```

**Step 2 — Use as a reference:**

**Tool:** `seedance_create_task`

```json
{
  "prompt": "A cat walking through the garden, cinematic shot",
  "videos": [
    {
      "kind": "url",
      "url": "<url from step 1>"
    }
  ],
  "resolution": "480p",
  "duration": 5
}
```

The presigned URL is valid for 30 minutes by default (configurable via
`TOS_PRESIGN_TTL_SECONDS` or `S3_PRESIGN_TTL_SECONDS` depending on the
selected backend). When the URL expires, use `media_presign` with the
`object_key` from the original upload to generate a fresh URL without
re-uploading. To renew many references at once, use `media_presign_batch`
with a list of `object_keys`. The bucket remains private; the presigned URL is
the only way for BytePlus to read the object.
