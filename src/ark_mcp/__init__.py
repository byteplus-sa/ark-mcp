"""Ark Seed Multimodal MCP Server.

A Python MCP server (FastMCP on uv) that exposes BytePlus multimodal
generation through a small, typed, safe tool surface:

- Seed Audio — full-scene audio generation through Seed Speech.
- Seedream — image generation and editing through ModelArk.
- Seedance — asynchronous video generation and task management through ModelArk.
- Durable artifacts — MCP resources for generated media whose provider URLs expire.
- Transports — local stdio first, with protected Streamable HTTP as a deployable option.
"""

from __future__ import annotations

from ark_mcp.server import create_server, mcp

__all__ = ["create_server", "mcp"]
__version__ = "0.2.0"
