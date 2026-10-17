"""Live smoke test: generate image, audio, and video through the MCP tools.

Run with: uv run python scripts/live_smoke_test.py

Exercises the full tool handler path for all three products:
1. Seedream — generate an image, persist to artifact store
2. Seed Audio — generate audio, persist to artifact store
3. Seedance — create video task using the generated image as reference,
   poll until succeeded, persist video

All artifacts are saved locally in .artifacts/ and verified retrievable
via the seed-media:// resource template.

Cost: ~$0.02 (image) + ~$0.003 (audio) + ~$0.05-0.10 (short 480p video)
"""

from __future__ import annotations

import asyncio
import base64
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import truststore

truststore.inject_into_ssl()

from _smoke_context import SmokeClient, require_tool_success, smoke_session  # noqa: E402

from ark_mcp.config.env import get_settings  # noqa: E402
from ark_mcp.tools.gen_task_get import (  # noqa: E402
    GenTaskGetInput,
    GenTaskGetOutput,
)
from ark_mcp.tools.seed_audio_generate import (  # noqa: E402
    SeedAudioGenerateInput,
    SeedAudioGenerateOutput,
)
from ark_mcp.tools.seedance_create_task import (  # noqa: E402
    SeedanceCreateTaskInput,
    SeedanceCreateTaskOutput,
    SeedanceImageInput,
)
from ark_mcp.tools.seedream_generate_image import (  # noqa: E402
    SeedreamGenerateInput,
    SeedreamGenerateOutput,
)

if TYPE_CHECKING:
    from ark_mcp.artifacts.store import ArtifactStore
    from ark_mcp.runtime import RuntimeServices

ARTIFACTS_DIR = Path(".artifacts")


def header(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}\n")


async def test_image_generation(ctx: SmokeClient, store: ArtifactStore) -> dict[str, str]:
    """Generate an image via seedream_generate_image and verify the artifact."""
    header("Seedream: Image Generation")
    print("Generating image: 'A serene mountain landscape at sunset, digital art'...")
    print(f"  Model: {get_settings().seedream_default_model}")
    print("  Size: 1024x1024")

    result = require_tool_success(
        await ctx.call(
            "seedream_generate_image",
            SeedreamGenerateInput(
                prompt="A serene mountain landscape at sunset, digital art",
                size="1024x1024",
                response_format="url",
                persist=True,
            ),
            SeedreamGenerateOutput,
        )
    )

    print(f"\n  Provider: {result.provider}")
    print(f"  Model: {result.model}")
    print(f"  Created: {result.created_at}")
    print(f"  Artifacts: {len(result.artifacts)}")
    print(f"  Item errors: {len(result.item_errors)}")
    print(f"  Usage: {result.usage.model_dump() if result.usage else None}")

    assert len(result.artifacts) >= 1, "Expected at least 1 artifact"
    artifact = result.artifacts[0]
    print(f"\n  Artifact ID: {artifact.id}")
    print(f"  Artifact URI: {artifact.uri}")
    print(f"  Media type: {artifact.media_type}")
    print(f"  MIME: {artifact.mime_type}")
    print(f"  Bytes: {artifact.bytes}")
    print(f"  SHA-256: {artifact.sha256}")

    # Verify the artifact is retrievable from the store.
    stored = await store.get(artifact.id)
    assert len(stored.data) > 0, "Artifact data should not be empty"
    print(f"  Verified: artifact retrieved from store ({len(stored.data)} bytes)")

    # Save a copy for easy viewing.
    ext = artifact.mime_type.split("/")[-1]
    output_path = ARTIFACTS_DIR / f"smoke_test_image.{ext}"
    output_path.write_bytes(stored.data)
    print(f"  Saved copy: {output_path}")

    return {"image": artifact.id}


async def test_audio_generation(ctx: SmokeClient, store: ArtifactStore) -> dict[str, str]:
    """Generate audio via seed_audio_generate and verify the artifact."""
    header("Seed Audio: Audio Generation")
    print("Generating audio: 'Welcome to the Ark Seed Multimodal MCP Server...'")

    result = require_tool_success(
        await ctx.call(
            "seed_audio_generate",
            SeedAudioGenerateInput(
                text_prompt=(
                    "Welcome to the Ark Seed Multimodal MCP Server. "
                    "This is a live smoke test of the Seed Audio generation tool. "
                    "The generated audio is persisted as a durable artifact."
                ),
                persist=True,
            ),
            SeedAudioGenerateOutput,
        )
    )

    print(f"\n  Provider: {result.provider}")
    print(f"  Model: {result.model}")
    print(f"  Duration: {result.duration_seconds}s")
    print(f"  Billing duration: {result.billing_duration_seconds}s")
    print(f"  Provider log ID: {result.provider_log_id}")

    assert result.artifact is not None, "Expected an artifact"
    artifact = result.artifact
    print(f"\n  Artifact ID: {artifact.id}")
    print(f"  Artifact URI: {artifact.uri}")
    print(f"  Media type: {artifact.media_type}")
    print(f"  MIME: {artifact.mime_type}")
    print(f"  Bytes: {artifact.bytes}")

    # Verify retrieval.
    stored = await store.get(artifact.id)
    assert len(stored.data) > 0, "Audio artifact should not be empty"
    print(f"  Verified: artifact retrieved from store ({len(stored.data)} bytes)")

    # Save a copy.
    output_path = ARTIFACTS_DIR / "smoke_test_audio.wav"
    output_path.write_bytes(stored.data)
    print(f"  Saved copy: {output_path}")

    return {"audio": artifact.id}


async def test_video_generation_with_image(
    ctx: SmokeClient,
    runtime: RuntimeServices,
) -> dict[str, str]:
    """Create a Seedance video task using a generated image as reference."""
    header("Seedance: Video Generation (with generated image)")

    # Step 1: Generate a reference image.
    print("Step 1: Generating reference image...")
    image_result = require_tool_success(
        await ctx.call(
            "seedream_generate_image",
            SeedreamGenerateInput(
                prompt="A fluffy orange cat sitting on a garden path, photorealistic",
                size="1024x1024",
                response_format="b64_json",
                persist=True,
            ),
            SeedreamGenerateOutput,
        )
    )

    assert len(image_result.artifacts) >= 1
    image_artifact = image_result.artifacts[0]
    print(f"  Reference image generated: {image_artifact.id}")

    # Get the image data as base64 for Seedance input.
    store = runtime.artifact_store
    stored_image = await store.get(image_artifact.id)
    image_b64 = base64.b64encode(stored_image.data).decode()

    # Step 2: Create the video task using the generated image.
    print("\nStep 2: Creating Seedance video task...")
    print(f"  Model: {get_settings().seedance_default_model}")
    print("  Resolution: 480p")
    print("  Duration: 5s")

    create_result = require_tool_success(
        await ctx.call(
            "seedance_create_task",
            SeedanceCreateTaskInput(
                prompt="The cat walks forward through the garden, gentle movement, warm sunlight",
                images=[
                    SeedanceImageInput(
                        kind="base64",
                        data=image_b64,
                        mime_type="image/png",
                        role="reference_image",
                    )
                ],
                resolution="480p",
                duration=5,
            ),
            SeedanceCreateTaskOutput,
        )
    )

    task_id = create_result.tasks[0].task_id
    if task_id is None:
        print(f"\n  Submission failed: {create_result.tasks[0].error}")
        return {"video": "submission_failed"}
    ctx.save_provider_ids(ARTIFACTS_DIR, [task_id])
    print(f"\n  Task ID: {task_id}")
    print(f"  Submitted: {create_result.submitted}")
    print(f"  Poll after: {create_result.recommended_poll_after_ms}ms")

    # Step 3: Poll until terminal.
    print("\nStep 3: Polling for completion...")
    max_wait_seconds = 300  # 5 minutes max
    poll_interval = 10  # seconds
    start_time = time.time()

    final_result = None
    while time.time() - start_time < max_wait_seconds:
        elapsed = int(time.time() - start_time)
        print(f"  [{elapsed}s] Polling task {task_id}...")

        result = require_tool_success(
            await ctx.call(
                "gen_task_get",
                GenTaskGetInput(task_ids=[task_id]),
                GenTaskGetOutput,
                background=False,
            )
        ).tasks[0]

        print(f"  [{elapsed}s] Status: {result.status}")

        if result.status in ("succeeded", "failed", "expired", "cancelled"):
            final_result = result
            break

        await asyncio.sleep(poll_interval)

    if final_result is None:
        print(f"\n  TIMEOUT: Task did not complete within {max_wait_seconds}s")
        print(f"  Task ID: {task_id} (check later with gen_task_get)")
        return {"video": "timeout"}

    print(f"\n  Final status: {final_result.status}")
    print(f"  Model: {final_result.model}")
    print(f"  Created: {final_result.created_at}")
    print(f"  Updated: {final_result.updated_at}")

    if final_result.error:
        print(f"  Error: {final_result.error}")

    if final_result.status == "succeeded":
        final_result = (
            await ctx.call(
                "gen_task_get",
                GenTaskGetInput(task_ids=[task_id], persist_output=True),
                GenTaskGetOutput,
            )
        ).tasks[0]

    artifact = next(
        (output.artifact for output in final_result.outputs if output.role == "video"), None
    )
    if artifact:
        print(f"\n  Video artifact ID: {artifact.id}")
        print(f"  Video artifact URI: {artifact.uri}")
        print(f"  Media type: {artifact.media_type}")
        print(f"  MIME: {artifact.mime_type}")
        print(f"  Bytes: {artifact.bytes}")

        # Verify retrieval.
        stored = await store.get(artifact.id)
        assert len(stored.data) > 0, "Video artifact should not be empty"
        print(f"  Verified: artifact retrieved from store ({len(stored.data)} bytes)")

        # Save a copy.
        output_path = ARTIFACTS_DIR / "smoke_test_video.mp4"
        output_path.write_bytes(stored.data)
        print(f"  Saved copy: {output_path}")

        return {"video": artifact.id}

    last_frame = next(
        (output.artifact for output in final_result.outputs if output.role == "last_frame"),
        None,
    )
    if last_frame:
        artifact = last_frame
        print(f"\n  Last frame artifact ID: {artifact.id}")
        stored = await store.get(artifact.id)
        output_path = ARTIFACTS_DIR / "smoke_test_last_frame.jpg"
        output_path.write_bytes(stored.data)
        print(f"  Saved copy: {output_path}")

    return {"video": final_result.status}


async def verify_artifacts(artifact_ids: dict[str, str], store: ArtifactStore) -> bool:
    """Verify all expected artifacts are retrievable from the store."""
    header("Artifact Verification")
    all_verified = True

    for media_type, artifact_id in artifact_ids.items():
        if artifact_id in ("skipped", "timeout") or artifact_id.startswith("failed"):
            print(f"  {media_type}: SKIPPED ({artifact_id})")
            continue

        print(f"  {media_type}: Retrieving artifact {artifact_id}...")
        try:
            artifact = await store.get(artifact_id)
            print(f"    Data: {len(artifact.data)} bytes")
            print(f"    MIME: {artifact.mime_type}")
            print(f"    Media type: {artifact.media_type}")
            print("    PASS")
        except Exception as exc:
            print(f"    FAIL: {exc}")
            all_verified = False

    # List all artifacts on disk.
    print("\n  Artifacts on disk:")
    if ARTIFACTS_DIR.exists():
        for path in sorted(ARTIFACTS_DIR.rglob("*")):
            if path.is_file() and not path.name.startswith("."):
                size_kb = path.stat().st_size / 1024
                print(f"    {path} ({size_kb:.1f} KB)")
    else:
        print("    (none)")

    return all_verified


async def main() -> int:
    header("Ark Seed MCP — Live Smoke Test")
    print(f"Timestamp: {datetime.now(UTC).isoformat()}")
    print(f"Artifacts dir: {ARTIFACTS_DIR.resolve()}")

    settings = get_settings()
    print(f"ModelArk configured: {settings.has_modelark}")
    print(f"Seed Audio configured: {settings.has_seed_audio}")
    print(f"Seedream model: {settings.seedream_default_model}")
    print(f"Seedance model: {settings.seedance_default_model}")

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    results: dict[str, str] = {}
    async with smoke_session(settings) as (ctx, runtime):
        # 1. Image generation
        try:
            results.update(await test_image_generation(ctx, runtime.artifact_store))
        except Exception as exc:
            print(f"\n  IMAGE FAILED: {exc}")
            import traceback

            traceback.print_exc()
            results["image"] = "failed"

        # 2. Audio generation
        try:
            results.update(await test_audio_generation(ctx, runtime.artifact_store))
        except Exception as exc:
            print(f"\n  AUDIO FAILED: {exc}")
            import traceback

            traceback.print_exc()
            results["audio"] = "failed"

        # 3. Video generation (uses the generated image as reference)
        try:
            results.update(await test_video_generation_with_image(ctx, runtime))
        except Exception as exc:
            print(f"\n  VIDEO FAILED: {exc}")
            import traceback

            traceback.print_exc()
            results["video"] = "failed"

        # 4. Verify all artifacts
        artifacts_verified = await verify_artifacts(results, runtime.artifact_store)

    # Summary
    header("Summary")
    for media_type, status in results.items():
        print(f"  {media_type}: {status}")

    expected_media = {"image", "audio", "video"}
    successful = (
        artifacts_verified
        and set(results) == expected_media
        and all(
            status not in {"failed", "timeout", "cancelled", "expired"}
            for status in results.values()
        )
    )
    return 0 if successful else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
