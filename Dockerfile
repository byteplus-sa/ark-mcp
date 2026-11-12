FROM ghcr.io/astral-sh/uv:0.11.26 AS uv

FROM python:3.12.13-slim AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

COPY --from=uv /uv /uvx /bin/
WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
RUN uv sync --locked --no-dev --no-editable

FROM python:3.12.13-slim AS runtime

ENV MCP_TRANSPORT=http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=3000 \
    MCP_AUTH_MODE=api_key \
    ARTIFACT_DIR=/app/.artifacts \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

RUN groupadd --system --gid 1001 modelark \
    && useradd --system --uid 1001 --gid modelark --create-home --home-dir /home/modelark modelark

WORKDIR /app
COPY --from=builder --chown=modelark:modelark /app/.venv /app/.venv
RUN mkdir -p /app/.artifacts && chown modelark:modelark /app/.artifacts

USER modelark
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:3000/health', timeout=3).read()"]

CMD ["/app/.venv/bin/python", "-m", "ark_mcp"]
