"""Live smoke test for the OmniHuman 2.0 provider adapter (needs BYTEPLUS_CV_API_KEY).

Submits one task through ``OmniHumanService``, then either cancels it
(``--cancel``) or polls until a terminal state and prints the result. This is
the only check that confirms the API-key endpoint accepts the documented body
and returns the documented envelope; the contract tests use fixtures.

Usage:
    BYTEPLUS_CV_API_KEY=... uv run python scripts/smoke_omnihuman.py \
        --image https://example.com/face.png --audio https://example.com/voice.mp3
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from ark_mcp.config.env import get_settings
from ark_mcp.domain.errors import ProviderError
from ark_mcp.providers.vision_cv.omnihuman import OmniHumanService
from ark_mcp.providers.vision_cv.schemas import OmniHumanSubmitRequest

TERMINAL_STATUSES = {"succeeded", "failed", "canceled", "expired", "not_found"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--image", required=True, help="Public HTTPS portrait image URL.")
    parser.add_argument("--audio", required=True, help="Public HTTPS wav/mp3 URL (4-300 s).")
    parser.add_argument("--prompt", default=None, help="Optional scene prompt.")
    parser.add_argument("--cancel", action="store_true", help="Cancel right after submitting.")
    parser.add_argument("--timeout", type=int, default=900, help="Polling timeout in seconds.")
    parser.add_argument("--interval", type=int, default=10, help="Polling interval in seconds.")
    return parser.parse_args()


async def run(args: argparse.Namespace) -> int:
    service = OmniHumanService()
    try:
        submission = await service.create(
            OmniHumanSubmitRequest.model_validate(
                {"image_url": args.image, "audio_url": args.audio, "prompt": args.prompt}
            )
        )
        print(f"submitted task_id={submission.task_id} request_id={submission.request_id}")

        if args.cancel:
            cancel = await service.cancel(submission.task_id)
            print(f"canceled={cancel.canceled} message={cancel.message}")
            return 0

        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            result = await service.get(submission.task_id)
            print(f"status={result.status}")
            if result.status in TERMINAL_STATUSES:
                print(f"video_url={result.video_url}")
                print(f"error={result.error_code} {result.error_message}")
                return 0 if result.status == "succeeded" else 1
            await asyncio.sleep(args.interval)
        print("timed out waiting for a terminal status", file=sys.stderr)
        return 2
    except ProviderError as exc:
        print(f"provider error: {exc.error.model_dump_json()}", file=sys.stderr)
        return 1
    finally:
        await service.close()


def main() -> int:
    args = parse_args()
    if not get_settings().cv_api_key:
        print("BYTEPLUS_CV_API_KEY is not set.", file=sys.stderr)
        return 2
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
