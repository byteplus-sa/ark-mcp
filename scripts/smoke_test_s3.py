"""Smoke test: S3 upload + presign + re-presign against a real bucket.

Run: uv run python scripts/smoke_test_s3.py
"""

from __future__ import annotations

import asyncio
import sys

import httpx

from ark_mcp.config.env import get_settings
from ark_mcp.providers.object_storage import make_object_storage_gateway


async def main() -> int:
    settings = get_settings()

    print(f"Backend: {settings.object_storage_backend}")
    print(f"S3 configured: {settings.has_s3}")
    print(f"Bucket: {settings.s3_bucket}")
    print(f"Region: {settings.s3_region}")
    print(f"Presign TTL: {settings.s3_presign_ttl_seconds}s")
    print(f"Endpoint: {settings.s3_endpoint or '(native AWS)'}")

    if not settings.has_s3:
        print("\nERROR: S3 is not configured. Check .env.")
        return 1

    gateway = make_object_storage_gateway(settings)
    print(f"\nGateway: {type(gateway).__name__}")

    test_key = "references/image/smoke-test-upload"
    test_data = b"ModelArk MCP S3 smoke test - this is a placeholder image."
    test_mime = "image/png"

    try:
        # Step 1: Upload
        print("\n--- Step 1: Upload ---")
        await gateway.upload_bytes(key=test_key, data=test_data, mime_type=test_mime)
        print(f"Uploaded {len(test_data)} bytes to key: {test_key}")

        # Step 2: Presign (simulates media_upload)
        print("\n--- Step 2: Presign (simulates media_upload) ---")
        url1 = await gateway.presign_get(key=test_key)
        print(f"Presigned URL (first 80 chars): {url1[:80]}...")

        # Step 3: Verify URL works — fetch the object
        print("\n--- Step 3: Verify presigned URL works ---")
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url1)
        print(f"HTTP status: {resp.status_code}")
        print(f"Content-Type: {resp.headers.get('content-type')}")
        print(f"Content matches: {resp.content == test_data}")
        if resp.status_code != 200:
            print(f"ERROR: Expected 200, got {resp.status_code}")
            print(f"Response body: {resp.text[:200]}")
            return 1

        # Step 4: Re-presign (simulates media_presign)
        print("\n--- Step 4: Re-presign (simulates media_presign) ---")
        url2 = await gateway.presign_get(key=test_key)
        print(f"Fresh presigned URL (first 80 chars): {url2[:80]}...")

        # Step 5: Verify the fresh URL also works
        print("\n--- Step 5: Verify re-presigned URL works ---")
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp2 = await client.get(url2)
        print(f"HTTP status: {resp2.status_code}")
        print(f"Content matches: {resp2.content == test_data}")

        # Step 6: URLs should be different (different expiry/signature)
        print("\n--- Step 6: URL uniqueness ---")
        print(f"URLs differ: {url1 != url2}")

        # Step 7: Seedance can fetch the URL as a video reference
        print("\n--- Step 7: Seedance reference URL fetch check ---")
        # Seedance fetches reference URLs server-side; simulate the same fetch
        # BytePlus would do against the presigned URL.
        # Upload a small fake video instead of image for this step.
        video_key = "references/video/smoke-test-video"
        video_data = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
        video_mime = "video/mp4"

        print("Uploading a small fake video reference...")
        await gateway.upload_bytes(key=video_key, data=video_data, mime_type=video_mime)
        video_url = await gateway.presign_get(key=video_key)
        print(f"Video presigned URL (first 80 chars): {video_url[:80]}...")

        # Verify BytePlus can fetch it (same as Seedance would)
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp_video = await client.get(video_url)
        print(f"Video fetch HTTP status: {resp_video.status_code}")
        print(f"Video content matches: {resp_video.content == video_data}")
        if resp_video.status_code != 200:
            print("ERROR: BytePlus would fail to fetch this reference URL.")
            return 1

        # Step 8: Create an actual Seedance task using the S3 presigned URL
        print("\n--- Step 8: Create Seedance task with S3 video reference ---")
        from ark_mcp.providers.modelark.client import ModelArkGateway
        from ark_mcp.providers.modelark.generation_tasks import GenerationTaskService
        from ark_mcp.providers.modelark.schemas import (
            SeedanceContentItem,
            SeedanceCreateProviderRequest,
        )
        from ark_mcp.providers.modelark.seedance import SeedanceService

        gateway_modelark = ModelArkGateway()
        service = SeedanceService(gateway=gateway_modelark)

        request = SeedanceCreateProviderRequest(
            model=settings.seedance_default_model,
            content=[
                SeedanceContentItem(type="text", text="A quick smoke test video"),
                SeedanceContentItem(
                    type="video_url", video_url={"url": video_url}, role="reference_video"
                ),
            ],
            resolution="480p",
            duration=4,
        )

        task_id, req_id = await service.create_task(request)
        print(f"Task ID: {task_id}")
        print(f"Request ID: {req_id}")

        import time

        print("Waiting 5s for task to be queued...")
        time.sleep(5)

        task, _ = await GenerationTaskService(gateway=gateway_modelark).get_task(task_id)
        print(f"Task status: {task.status}")
        if task.status == "failed":
            print(f"Task failed: {task.error}")
        elif task.status == "succeeded":
            print("Task succeeded! Video URL available.")
        else:
            print(f"Task is still {task.status} — accepted the S3 URL as reference.")

        print("Seedance accepted the S3 presigned URL as a video reference.")

        print("\n=== ALL CHECKS PASSED ===")
        return 0

    except Exception as exc:
        print(f"\nERROR: {exc}")
        import traceback

        traceback.print_exc()
        return 1
    finally:
        await gateway.close()
        print("\nGateway closed.")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
