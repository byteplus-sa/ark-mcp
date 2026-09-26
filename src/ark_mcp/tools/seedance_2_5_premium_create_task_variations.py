"""``seedance_2_5_premium_create_task_variations`` tool — parallel Seedance 2.5 Premium tasks.

Creates N independent Seedance 2.5 Premium (4K) video generation tasks in
parallel. Registered only when ``BYTEPLUS_MODELARK_SEEDANCE_2_5_PREMIUM_ENABLED``
is true.
"""

from __future__ import annotations

from typing import Literal

from fastmcp import Context
from pydantic import Field

from ark_mcp.config.model_capabilities import ModelFamily
from ark_mcp.tools.seedance_2_5_create_task_variations import (
    TOOL_ANNOTATIONS,
    Seedance25VariationsInput,
    Seedance25VariationsOutput,
    run_seedance_2_5_variations,
)
from ark_mcp.tools.seedance_2_5_premium_create_task import require_seedance_2_5_premium

__all__ = [
    "TOOL_ANNOTATIONS",
    "Seedance25PremiumVariationsInput",
    "Seedance25VariationsOutput",
    "seedance_2_5_premium_create_task_variations",
]


class Seedance25PremiumVariationsInput(Seedance25VariationsInput):
    """Input for parallel Seedance 2.5 Premium video task creation.

    Same fields and validators as ``seedance_2_5_create_task_variations``,
    with 4k resolution and the Premium model binding.
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


async def seedance_2_5_premium_create_task_variations(
    input: Seedance25PremiumVariationsInput, ctx: Context
) -> Seedance25VariationsOutput:
    """Create multiple Seedance 2.5 Premium (4K) video tasks in parallel.

    Seedance 2.5 Premium is a whitelist-only model separate from regular
    Seedance 2.5. Each variation creates a separate task; the caller polls
    each task ID via ``seedance_get_task``. Partial failures are captured per
    variation. Requires MCP task-augmented execution for the provider
    submissions.
    """
    require_seedance_2_5_premium()
    return await run_seedance_2_5_variations(input, ctx, ModelFamily.SEEDANCE_2_5_PREMIUM)
