"""``seedance_2_5_premium_create_task`` tool — create a Seedance 2.5 Premium video task.

Seedance 2.5 Premium is a whitelist-only model, separate from regular
Seedance 2.5, that adds 4K output. It shares the 2.5 input surface (30-second
duration, 30 images / 10 videos / 10 audio references) and is registered only
when ``BYTEPLUS_MODELARK_SEEDANCE_2_5_PREMIUM_ENABLED`` is true.
"""

from __future__ import annotations

from typing import Literal

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import Field

from ark_mcp.config.env import get_settings
from ark_mcp.config.model_capabilities import ModelFamily
from ark_mcp.tools.seedance_2_5_create_task import (
    TOOL_ANNOTATIONS,
    Seedance25CreateTaskInput,
    Seedance25CreateTaskOutput,
    run_seedance_2_5_create,
)

__all__ = [
    "TOOL_ANNOTATIONS",
    "Seedance25CreateTaskOutput",
    "Seedance25PremiumCreateTaskInput",
    "require_seedance_2_5_premium",
    "seedance_2_5_premium_create_task",
]

PREMIUM_DISABLED_MESSAGE = (
    "Seedance 2.5 Premium is whitelist-only and disabled. Set "
    "BYTEPLUS_MODELARK_SEEDANCE_2_5_PREMIUM_ENABLED=true in .env once the "
    "account is whitelisted."
)


class Seedance25PremiumCreateTaskInput(Seedance25CreateTaskInput):
    """Input model for ``seedance_2_5_premium_create_task``.

    Same fields and validators as ``seedance_2_5_create_task``, with 4k
    resolution and the Premium model binding.
    """

    model: str | None = Field(
        None,
        description=(
            "Seedance 2.5 Premium model ID. Defaults to the configured Premium binding "
            "(SEEDANCE_2_5_PREMIUM_MODEL, 'dreamina-seedance-2-5-premium-260915'). "
            "Omit to use the default."
        ),
    )
    # Premium widens the 2.5 resolution set with 4k.
    resolution: Literal["480p", "720p", "1080p", "4k"] | None = Field(  # type: ignore[assignment]
        None,
        description=(
            "Output video resolution. Seedance 2.5 Premium supports 480p, 720p, 1080p, and 4k."
        ),
    )


def require_seedance_2_5_premium() -> None:
    """Raise when the Seedance 2.5 Premium feature flag is off."""
    if not get_settings().seedance_2_5_premium_enabled:
        raise ValueError(PREMIUM_DISABLED_MESSAGE)


async def seedance_2_5_premium_create_task(
    input: Seedance25PremiumCreateTaskInput, ctx: Context
) -> Seedance25CreateTaskOutput | ToolResult:
    """Create an asynchronous Seedance 2.5 Premium (4K) video generation task.

    Seedance 2.5 Premium is a whitelist-only model separate from regular
    Seedance 2.5; use it when 4k output is required. Accepts text, image,
    video, and audio references, up to 30-second duration, 50 multimodal
    references (30 images, 10 videos, 10 audio), and 480p/720p/1080p/4k
    resolution. The task runs asynchronously on the provider — use
    ``seedance_get_task`` to poll for completion. Returns the task ID and a
    recommended polling interval. Requires MCP task-augmented execution for
    the provider submission itself.
    """
    require_seedance_2_5_premium()
    return await run_seedance_2_5_create(input, ctx, ModelFamily.SEEDANCE_2_5_PREMIUM)
