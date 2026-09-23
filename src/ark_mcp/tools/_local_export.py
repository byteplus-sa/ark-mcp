"""``output_path`` / ``output_dir`` support shared by generation and task tools.

``@local_export(...)`` wraps a tool handler:

1. before the handler runs (and therefore before any billable provider
   call), it validates ``output_path``/``output_dir`` against the output-root
   policy, so a bad path never produces paid output that cannot be written;
2. after the handler returns, it writes each named artifact field to the
   destination and returns the result with ``ArtifactRef.local_path`` (or
   ``export_error``) set. A failed local write never fails the call.

Named fields may hold an ``ArtifactRef``, a list of them, or a
``VariationSummary`` (whose variations' artifacts are exported).
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from fastmcp import Context
from pydantic import BaseModel, Field

from ark_mcp.artifacts.export import LocalExportTarget, export_ref, prepare_local_export
from ark_mcp.config.env import get_settings
from ark_mcp.domain.artifacts import ArtifactRef
from ark_mcp.domain.models import VariationSummary
from ark_mcp.runtime import get_principal, get_runtime

InputT = TypeVar("InputT", bound=BaseModel)
ResultT = TypeVar("ResultT")

_OUTPUT_ROOT_NOTE = (
    "stdio transport only; must be absolute and inside an allowed output root (the client's "
    "MCP roots, or OUTPUT_ROOTS). Validated before any provider call. The durable artifact is "
    "always kept; the local copy is reported in ArtifactRef.local_path (or export_error)."
)


def output_path_field(what: str = "the output") -> Any:
    """Field for an optional local file (or directory, ending in '/') destination."""
    return Field(
        None,
        description=(
            f"Optional local destination for {what}: a file path, or a directory when it ends "
            f"with '/', where files are named <artifact_id>.<ext>. {_OUTPUT_ROOT_NOTE}"
        ),
    )


def output_dir_field(what: str = "each output") -> Any:
    """Field for an optional local directory destination."""
    return Field(
        None,
        description=(
            f"Optional local directory where {what} is written as <artifact_id>.<ext>. "
            f"{_OUTPUT_ROOT_NOTE}"
        ),
    )


def overwrite_field() -> Any:
    """Field allowing a local destination file to be replaced."""
    return Field(
        False,
        description=(
            "Allow output_path/output_dir to replace an existing file with different content. "
            "An existing file with identical content is always accepted."
        ),
    )


def _suffix(label: str, position: int, target: LocalExportTarget) -> str:
    if target.is_dir or position == 0:
        return ""
    return f"-{label}"


async def _apply(
    result: BaseModel, fields: tuple[str, ...], target: LocalExportTarget, ctx: Context
) -> BaseModel:
    runtime = get_runtime(ctx)
    auth = get_principal(ctx)
    store = runtime.artifact_store
    update: dict[str, Any] = {}
    position = 0

    async def one(ref: ArtifactRef | None, label: str) -> ArtifactRef | None:
        nonlocal position
        if ref is None:
            return None
        exported = await export_ref(
            store, ref, target, auth=auth, suffix=_suffix(label, position, target)
        )
        position += 1
        return exported

    for field in fields:
        value = getattr(result, field, None)
        if isinstance(value, ArtifactRef):
            update[field] = await one(value, field.replace("_", "-"))
        elif isinstance(value, list):
            update[field] = [
                await one(item, str(index + 1)) if isinstance(item, ArtifactRef) else item
                for index, item in enumerate(value)
            ]
        elif isinstance(value, VariationSummary):
            variations = []
            for variation in value.variations:
                exported = await one(variation.artifact, str(variation.index))
                variations.append(variation.model_copy(update={"artifact": exported}))
            update[field] = value.model_copy(update={"variations": variations})
    return result.model_copy(update=update)


def local_export(
    *fields: str,
    require_dir: Callable[[Any], bool] | None = None,
    precondition: Callable[[Any], str | None] | None = None,
) -> Callable[
    [Callable[[InputT, Context], Awaitable[ResultT]]],
    Callable[[InputT, Context], Awaitable[ResultT]],
]:
    """Add ``output_path``/``output_dir`` handling to a tool handler.

    ``require_dir(input)`` returns True when the call can produce several
    artifacts, so a file path is rejected. ``precondition(input)`` returns an
    error message when a destination is not usable for this input.
    """

    def decorator(
        handler: Callable[[InputT, Context], Awaitable[ResultT]],
    ) -> Callable[[InputT, Context], Awaitable[ResultT]]:
        @functools.wraps(handler)
        async def wrapper(input: InputT, ctx: Context) -> ResultT:
            output_path = getattr(input, "output_path", None)
            output_dir = getattr(input, "output_dir", None)
            target: LocalExportTarget | None = None
            if output_path is not None or output_dir is not None:
                if precondition is not None and (message := precondition(input)):
                    raise ValueError(message)
                target = await prepare_local_export(
                    output_path=output_path,
                    output_dir=output_dir,
                    overwrite=bool(getattr(input, "overwrite", False)),
                    ctx=ctx,
                    settings=get_settings(),
                    require_dir=bool(require_dir and require_dir(input)),
                )
            result = await handler(input, ctx)
            if target is None or not isinstance(result, BaseModel):
                return result
            return await _apply(result, fields, target, ctx)  # type: ignore[return-value]

        return wrapper

    return decorator


def needs_persist(input: Any) -> str | None:
    """Precondition: a local copy needs a persisted artifact (persist=true)."""
    if getattr(input, "persist", True) is False:
        return "output_path/output_dir require persist=true."
    return None


def needs_persist_output(input: Any) -> str | None:
    """Precondition: a local copy needs a persisted artifact (persist_output=true)."""
    if getattr(input, "persist_output", True) is False:
        return "output_path requires persist_output=true."
    return None
