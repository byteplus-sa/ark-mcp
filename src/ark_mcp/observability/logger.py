"""Structured stderr logger for the Ark Seed MCP server.

Only MCP JSON-RPC messages go to stdout. All structured logs go to stderr,
as required by the ``stdio`` transport specification.

Never log: prompt text, full media URLs, Base64, subtitles, or credentials.
Supports log level filtering via the ``ARK_LOG_LEVEL`` environment variable
(default: INFO).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from typing import Any

_REDACT_KEYS: frozenset[str] = frozenset(
    {
        "authorization",
        "x-api-key",
        "api_key",
        "apikey",
        "token",
        "secret",
        "password",
        "safety_identifier",
        "b64_json",
        "audio_data",
        "image_data",
        "base64",
        "prompt",
        "text_prompt",
        "variation_prompts",
        "subtitle",
        "subtitle_text",
        "subtitles",
        "url",
        "media_url",
        "audio_url",
        "image_url",
        "video_url",
    }
)

_LEVELS: dict[str, int] = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}
_CURRENT_LEVEL: int = _LEVELS.get(os.environ.get("ARK_LOG_LEVEL", "INFO").upper(), 20)


def set_level(level: str) -> None:
    """Set the minimum log level at runtime."""
    global _CURRENT_LEVEL
    normalized = level.upper()
    if normalized not in _LEVELS:
        raise ValueError(f"Invalid log level '{level}'. Allowed: {sorted(_LEVELS)}")
    _CURRENT_LEVEL = _LEVELS[normalized]


def get_level() -> str:
    """Return the current log level name."""
    for name, val in _LEVELS.items():
        if val == _CURRENT_LEVEL:
            return name
    return "INFO"


def _redact(value: Any) -> Any:
    """Recursively redact sensitive keys from dict/list/tuple/set structures."""
    if isinstance(value, dict):
        return {
            k: ("[REDACTED]" if k.lower() in _REDACT_KEYS else _redact(v)) for k, v in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return type(value)(_redact(item) for item in value)
    return value


def _emit(level: str, message: str, **fields: Any) -> None:
    """Emit a structured JSON log line to stderr if level is enabled."""
    level_val = _LEVELS.get(level, 20)
    if level_val < _CURRENT_LEVEL:
        return
    record: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(),
        "level": level,
        "msg": message,
    }
    record.update(_redact(fields))
    # stderr only — stdout is reserved for MCP JSON-RPC.
    sys.stderr.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")
    sys.stderr.flush()


def debug(message: str, **fields: Any) -> None:
    _emit("DEBUG", message, **fields)


def info(message: str, **fields: Any) -> None:
    _emit("INFO", message, **fields)


def warning(message: str, **fields: Any) -> None:
    _emit("WARNING", message, **fields)


def error(message: str, **fields: Any) -> None:
    _emit("ERROR", message, **fields)
