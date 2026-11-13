---
name: fastmcp
description: Build production-grade MCP (Model Context Protocol) servers, clients, and apps in Python with FastMCP v4. Use when creating or editing FastMCP servers, upgrading from v3, or working with tools, resources, prompts, providers, transforms, middleware, auth, background tasks, sampling, or deployment.
---

# FastMCP v4 — Build MCP Servers, Clients, and Apps in Python

FastMCP is the standard Python framework for building Model Context Protocol (MCP)
applications. MCP is the open protocol that connects LLMs to tools and data; FastMCP
makes it Pythonic — declare a tool with a plain function and the schema, validation,
and documentation are generated automatically.

FastMCP 1.0 was incorporated into the official MCP Python SDK in 2024. The actively
maintained standalone project is made by the Prefect team and powers the majority of
MCP servers across all languages. The repository moved from `jlowin/fastmcp` to
[`PrefectHQ/fastmcp`](https://github.com/PrefectHQ/fastmcp) under Prefect's
stewardship as part of the v3 release.

Three pillars:

- **Servers** — expose `tools`, `resources`, and `prompts` to LLMs via the `FastMCP` class.
- **Clients** — connect to any MCP server (local or remote, programmatic or CLI) with full protocol support.
- **Apps** — give tools interactive UIs rendered directly in the conversation.

> **Project runtime: `v4.1.8` (November 12, 2026).** This skill targets
> `fastmcp>=4.1.8,<4.2`.
> Docs reflect the `main` branch; features are marked with version badges (e.g.
> `New in version: 3.0.0`). Verify unreleased features against live docs.

## Quick Start

### Installation

```bash
pip install fastmcp
# or
uv add fastmcp

# Optional extras
pip install "fastmcp[tasks]"              # Background tasks (Docket scheduler)
pip install 'py-key-value-aio[redis]'    # Redis storage backend
```

### Minimal Server

```python
from fastmcp import FastMCP

mcp = FastMCP("My Server")  # MUST be at module level for cloud deployment

@mcp.tool
def hello(name: str) -> str:
    """Say hello to someone."""
    return f"Hello, {name}!"

if __name__ == "__main__":
    mcp.run()
```

> **v3/v4 note:** Decorators (`@mcp.tool`, `@mcp.resource`, `@mcp.prompt`) no longer
> require parentheses, and they return the **original function**, not a component
> object. Code that accesses `.name` / `.description` on the decorated result will
> crash. In v4, access component metadata through the server; the old decorator
> compatibility mode has been removed.

### Run It

```bash
# Local development (stdio, default)
python server.py

# With FastMCP CLI inspector
fastmcp dev server.py

# HTTP mode (v3: pass transport/host/port to run(), NOT the constructor)
python server.py  # where server calls mcp.run(transport="http", host="0.0.0.0", port=8000)

# Debug logging
FASTMCP_LOG_LEVEL=DEBUG fastmcp dev server.py

# Install to Claude Desktop / Cursor / Gemini
fastmcp install server.py
```

## FastMCP 4.1 Upgrade Notes

The project pins the patched 4.1 line. Version 4.1 tightens MultiAuth identity
scopes, OpenAPI path parameters, skill-directory containment and regex tool
search. Stateful HTTP sessions now inherit the SDK's 30-minute idle timeout;
clients must reconnect expired sessions. Drain pending tasks before upgrading.

Version 4.1.2 fixes authorization after component transforms and denied-resource
template fallback. OAuth client-credentials providers must explicitly configure
`issuer` for issuer binding; upgrading alone does not enable that protection.
This project uses its JWT verifier rather than those OAuth providers.

Sources: [4.1.0](https://github.com/PrefectHQ/fastmcp/releases/tag/v4.1.0),
[4.1.2](https://github.com/PrefectHQ/fastmcp/releases/tag/v4.1.2),
[4.1.6](https://github.com/PrefectHQ/fastmcp/releases/tag/v4.1.6).

## What's New in v4 (vs v3)

FastMCP v4 is based on MCP Python SDK v2. Most v3 applications upgrade without
changes, but direct protocol-model attribute access must use Python
`snake_case`; JSON wire payloads remain camelCase. The release also removes
previously deprecated APIs and moves FastMCP's internal client transport to
`httpx2`. Application-owned `httpx` clients are unaffected.

Before upgrading, search for deprecated context sampling/root methods, old
constructor parameters, FastMCP-internal `httpx` exception handling, and
camelCase protocol-model attributes. Run protocol discovery and transport smoke
tests in addition to unit tests.

Official upgrade guide:
<https://gofastmcp.com/getting-started/upgrading/from-fastmcp-3>

## What's New in v3 (vs v2)

v3.0.0 (February 18, 2026) is a major architectural refactor. For most servers,
upgrading is straightforward — the breaking changes affect deprecated constructor
kwargs, sync-to-async shifts, a few renamed methods, and less commonly used features.

### v3.4.x Highlights (June–July 2026)

| Version | Date | Headline |
| --- | --- | --- |
| **v3.4.4** | 2026-07-09 | "Host in Translation" — restores HTTP deployment compatibility after 3.4.3 Host/Origin guard; adds Hugging Face OAuth provider. |
| **v3.4.3** | 2026-07-05 | "The Fast and the Secure-ious" — SSRF hardening (NAT64/6to4/Teredo/ISATAP), Streamable HTTP Host/Origin validation (DNS rebinding), OAuth redirect validation. |
| **v3.4.2** | 2026-06-06 | "Heads Up" — JWT compatibility for private, non-critical JWS header params (e.g. Clerk's `cat`). |
| **v3.4.1** | 2026-06-05 | "Floor It" — Starlette floored at `>=1.0.1` (CVE-2026-48710); OAuthProxy logs refresh-token cache misses. |
| **v3.4.0** | 2026-06-03 | "Remote Control" — `fastmcp-remote` bridge connects stdio-only MCP hosts to HTTP servers; proxy hardening; long-lived access tokens. |
| **v3.3.0** | 2026-05-15 | "Slim Reaper" — `fastmcp-slim` lightweight client-only distribution; security & observability hardening. |

### v3.0.0 Architectural Changes

**Provider architecture** — All components are sourced via providers:

| Provider | Purpose | How you use it |
| --- | --- | --- |
| `LocalProvider` | Stores components you define in code (default) | `@mcp.tool`, `mcp.add_tool()` |
| `FastMCPProvider` | Wraps another FastMCP server | `mcp.mount(server)` |
| `ProxyProvider` | Connects to remote MCP servers | `create_proxy(client)` |
| `FileSystemProvider` | Discover decorated functions from directories (hot-reload) | `mcp.add_provider(FileSystemProvider(path="./tools", reload=True))` |
| `OpenAPIProvider` | Auto-generate from OpenAPI specs | `FastMCP("name", providers=[OpenAPIProvider(...)])` |
| `SkillsProvider` | Expose agent skill files as MCP resources | — |
| Custom | Database-backed, dynamic sources | Subclass `LocalProvider` |

**Transforms** (component middleware) — Modify components as they flow from providers
to clients. Provider-level (specific source) or server-level (all components).

| Transform | Purpose |
| --- | --- |
| `Namespace` | Prefix component names to prevent conflicts |
| `ToolTransform` | Rename tools, modify descriptions, reshape arguments |
| `VersionFilter` | Filter components by version (`version_gte`, `version_lt`) |
| `Enabled` | Control which components are visible at runtime |
| `ToolSearch` | Replace large tool catalogs with on-demand search |
| `ResourcesAsTools` | Expose resources to tool-only clients |
| `PromptsAsTools` | Expose prompts to tool-only clients |
| `CodeMode` (experimental) | Let LLMs write Python to orchestrate tools in a sandbox |

**Component versioning** — `@mcp.tool(version="2.0")`; clients see the highest version
by default. Use `VersionFilter` to serve different API versions from one codebase.

**Session-scoped state** — `await ctx.set_state()` / `await ctx.get_state()` are now
**async** (must be awaited). Values must be JSON-serializable unless
`serializable=False` is passed. Each `FastMCP` instance has its own isolated state
store — shared state across mounts requires passing the same `session_state_store`.

### ⚠️ v3.0.0 Breaking Changes

1. **Constructor kwargs removed** — `host`, `port`, `log_level`, `debug`, `sse_path`,
   `streamable_http_path`, `json_response`, `stateless_http`, `message_path`,
   `on_duplicate_tools/resources/prompts`, `tool_serializer`, `include_tags`,
   `exclude_tags`, `tool_transformations`. Pass these to `run()` / `run_http_async()`
   or use the new unified APIs (`on_duplicate=`, `server.enable()/disable()`,
   `add_transform()`). `message_path` is env-var only (`FASTMCP_MESSAGE_PATH`).
2. **Component methods removed** — `tool.enable()/disable()` raises
   `NotImplementedError`; `get_tools()/get_resources()/get_prompts()/
   get_resource_templates()` removed. Use `list_tools()/list_resources()/
   list_prompts()/list_resource_templates()` (return lists, not dicts).
3. **Async state** — `ctx.set_state()`/`ctx.get_state()` are now async.
4. **Prompts** — `mcp.types.PromptMessage` replaced by `fastmcp.prompts.Message`.
   `Message("Hello")` (role defaults to `"user"`, accepts plain strings). v2 silently
   coerced dicts; v3 requires typed `Message` objects or plain strings.
5. **Auth providers** — No longer auto-load from env vars. Pass `client_id`,
   `client_secret` explicitly via `os.environ`.
6. **OpenAPI** — `timeout` parameter removed from `OpenAPIProvider`. Set timeout on
   the `httpx.AsyncClient` instead.
7. **Metadata** — Namespace changed from `"_fastmcp"` to `"fastmcp"` in `tool.meta`.
   `include_fastmcp_meta` removed (always included).
8. **Env var** — `FASTMCP_SHOW_CLI_BANNER` renamed to `FASTMCP_SHOW_SERVER_BANNER`.
9. **Decorators return functions** — `@mcp.tool` returns the original function, not a
   component object. Access component metadata through the server.
10. **OAuth storage** — Default OAuth client storage changed from `DiskStore` to
    `FileTreeStore` (CVE-2025-69872 pickle deserialization vulnerability in diskcache).
    Clients re-register automatically on first connection.
11. **Repo move** — `jlowin/fastmcp` → `PrefectHQ/fastmcp`. Update git remotes and
    `git+https://...` dependency URLs.
12. **Background tasks** — install `fastmcp[tasks]`, register `TasksExtension`, and
    use `task=True` or `TaskConfig` only on tools.

### Removed v3 Compatibility APIs

Replace these v3-era APIs before running on v4; the old forms no longer work:

- `mount(prefix="x")` → `mount(namespace="x")`
- `import_server(sub)` → `mount(sub)`
- `FastMCP.as_proxy(url)` → `from fastmcp.server import create_proxy; create_proxy(url)`
- `from fastmcp.server.proxy` → `from fastmcp.server.providers.proxy`
- `from fastmcp.server.openapi import FastMCPOpenAPI` →
  `from fastmcp.server.providers.openapi import OpenAPIProvider`
- `mcp.add_tool_transformation(name, cfg)` →
  `from fastmcp.server.transforms import ToolTransform; mcp.add_transform(ToolTransform(...))`

### Migration

```bash
# Upgrade an existing install (pip install fastmcp won't upgrade an existing pin)
pip install --upgrade fastmcp
# or
uv add --upgrade fastmcp
```

Pin in `requirements.txt` / `pyproject.toml`:

```
fastmcp>=4.1.8,<4.2
```

Most v3 servers upgrade to v4 without code changes. MCP Python SDK v2 model
attributes use `snake_case`; update direct attribute access such as
`inputSchema` to `input_schema`. FastMCP's internal HTTP stack uses `httpx2`,
while HTTP calls made by application tools continue to use the application's
chosen client.

For most servers, the import remains unchanged:

```python
# v3.x and v4.x compatible
from fastmcp import FastMCP

mcp = FastMCP("server")
# ... rest of code works the same
```

Migration guides:

- v3 to v4: <https://gofastmcp.com/getting-started/upgrading/from-fastmcp-3>
- v2 to v3: <https://gofastmcp.com/getting-started/upgrading/from-fastmcp-2>

## Core Concepts

### Tools

Functions LLMs can call. Best practices: clear names, comprehensive docstrings (LLMs
read these!), strong type hints (Pydantic validates), structured returns, error
handling.

```python
from fastmcp import FastMCP
import httpx

mcp = FastMCP("API Server")

@mcp.tool
async def fetch_user(user_id: str) -> dict:  # Use async for I/O
    """Fetch a user profile by ID."""
    async with httpx.AsyncClient() as client:
        r = await client.get(f"https://api.example.com/users/{user_id}")
        r.raise_for_status()
        return r.json()
```

### Resources & Templates

Expose data to LLMs. URI schemes: `data://`, `file://`, `resource://`, `info://`,
`api://`, or custom.

```python
@mcp.resource("data://config")
def get_config() -> dict:
    return {"theme": "dark", "version": "1.0"}

@mcp.resource("user://{user_id}/profile")  # Template with parameters
async def get_user(user_id: str) -> dict:  # CRITICAL: param names must match URI
    return await fetch_user_from_db(user_id)
```

### Prompts

Pre-configured message templates with parameters.

```python
from fastmcp.prompts import Message  # v3: NOT mcp.types.PromptMessage

@mcp.prompt
def analyze(data_points: list[float]) -> list[Message]:
    formatted = ", ".join(str(p) for p in data_points)
    return [
        Message(f"Please analyze these data points: {formatted}"),
        Message("Consider trends and outliers.", role="assistant"),
    ]
```

### Context

Inject `Context` for logging, progress, resources, sampling, elicitation, and state.

**Preferred (v3):** `CurrentContext()` dependency:

```python
from fastmcp import FastMCP
from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context

mcp = FastMCP(name="Context Demo")

@mcp.tool
async def process_file(file_uri: str, ctx: Context = CurrentContext()) -> str:
    """Process a file, using context for logging and resource access."""
    await ctx.info(f"Processing {file_uri}")
    return "Processed file"
```

**Legacy (still works):** type-hint injection:

```python
@mcp.tool
async def process_file(file_uri: str, ctx: Context) -> str:
    # Context injected automatically based on the type hint
    return "Processed file"
```

Dependency parameters are automatically excluded from the MCP schema. Context methods
are async; each request gets a fresh context object.

### Elicitation (User Input)

On modern `2026-07-28` connections, use the multi-round guard pattern: return an
`InputRequiredResult` that describes the required input, then resume when the
client calls the tool again with the answer. Taking confirmation as an explicit
tool argument is simpler when the caller can provide it directly.

`ctx.elicit(prompt, response_type=...)` remains available only on legacy
handshake-era connections and requires an explicit `response_type`. A default
FastMCP v4 client negotiates the modern protocol, where `ctx.elicit()` raises.
See [Elicitation](https://gofastmcp.com/servers/elicitation#which-approach-to-use)
before choosing between the two protocol shapes.

### Progress Reporting

```python
@mcp.tool
async def batch_import(file_path: str, ctx: Context) -> dict:
    data = await read_file(file_path)
    for i, item in enumerate(data):
        await ctx.report_progress(i + 1, len(data), f"Importing {i + 1}/{len(data)}")
        await import_item(item)
    return {"imported": len(data)}
```

### Sampling (LLM calls from tools)

FastMCP v4 removes `ctx.sample()` and `ctx.sample_step()`. Call an LLM directly
from the server when the application owns the model. When the purpose is to use
the caller's model, return an `InputRequiredResult` carrying a sampling request
and resume on the next tool call. Client-side `sampling_handler=` remains
supported. See [Sampling](https://gofastmcp.com/servers/sampling).

## Background Tasks (v4 extension)

Protocol-native background tasks (SEP-2663) powered by Docket. Requires:

```bash
pip install "fastmcp[tasks]"
```

Register `TasksExtension`, then add `task=True` (or `TaskConfig`) to a tool.
Background tasks require async functions; `task=` is not valid on resources or
prompts in v4.

```python
import asyncio
from fastmcp import FastMCP
from fastmcp.utilities.tasks import TaskConfig
from fastmcp_tasks import TasksExtension

mcp = FastMCP("MyServer")
mcp.add_extension(TasksExtension())

@mcp.tool(task=True)  # Supports both sync and background execution (mode="optional")
async def slow_computation(duration: int) -> str:
    """A long-running operation."""
    for i in range(duration):
        await asyncio.sleep(1)
    return f"Completed in {duration} seconds"

# Execution modes via TaskConfig:
#   "forbidden" — sync only; errors if client requests background
#   "optional"  — sync or background (default when task=True)
#   "required"  — background only; errors if client doesn't request task
@mcp.tool(task=TaskConfig(mode="required"))
async def must_be_background() -> str:
    return "Only runs as a background task"
```

Task states: `pending → running → completed / failed / cancelled`. Tasks execute
through the Docket scheduler — cannot execute tasks through proxies (raises error).

**When to use:** Operations taking >30 seconds, batch processing with per-item status,
operations that may need user input mid-execution.

## Server Lifespans

Lifespans run **once per server instance** (not per session) — critical for DB
connections, API clients. v3 introduces the composable `@lifespan` decorator.

```python
from fastmcp import FastMCP, Context
from fastmcp.server.lifespan import lifespan

@lifespan
async def app_lifespan(server):
    # Setup: runs once when server starts
    db = await Database.connect()
    api_client = httpx.AsyncClient(timeout=30.0)
    try:
        yield {"db": db, "api_client": api_client}
    finally:
        # Teardown: runs when server stops
        await db.disconnect()
        await api_client.aclose()

mcp = FastMCP("Server", lifespan=app_lifespan)

@mcp.tool
async def query_db(sql: str, ctx: Context) -> list:
    db = ctx.lifespan_context["db"]
    return await db.query(sql)
```

**Compose multiple lifespans** with the `|` operator (enter left→right, exit
right→left, merge dicts). Legacy `@asynccontextmanager` lifespans still work; wrap
with `ContextManagerLifespan` for composition.

**FastAPI integration** — use `combine_lifespans`:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastmcp import FastMCP
from fastmcp.utilities.lifespan import combine_lifespans

@asynccontextmanager
async def app_lifespan(app):
    yield

mcp = FastMCP("Tools")
mcp_app = mcp.http_app()
app = FastAPI(lifespan=combine_lifespans(app_lifespan, mcp_app.lifespan))
app.mount("/mcp", mcp_app)
```

## Middleware

Middleware intercepts and modifies every MCP message. It is FastMCP-specific (not
part of the MCP spec). v3 uses the `Middleware` base class with `on_message` hooks.

```python
from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware, MiddlewareContext

class LoggingMiddleware(Middleware):
    async def on_message(self, context: MiddlewareContext, call_next):
        print(f"→ {context.method}")
        result = await call_next(context)
        print(f"← {context.method}")
        return result

mcp = FastMCP("MyServer")
mcp.add_middleware(LoggingMiddleware())
```

**Execution order:** First added runs first on the way in, last on the way out.
Place error handling early; logging late. Built-in middleware (import from
`fastmcp.server.middleware.*`): `ErrorHandlingMiddleware`, `RateLimitingMiddleware`,
`LoggingMiddleware`, `ResponseCachingMiddleware` (use `cache_storage=`), plus
timing, tool injection, prompt-tool, and resource-tool variants.

**Mounted servers:** Parent middleware runs for all requests; child middleware only
runs for the child's tools. State does not cross mount boundaries automatically —
pass the same `session_state_store` or use `serializable=False`.

Hook hierarchy: `on_message` (all) → `on_request`/`on_notification` →
`on_call_tool`/`on_read_resource`/`on_get_prompt` → `on_list_*`.

## Server Composition

Use `mount()` (dynamic) for live runtime links. FastMCP v4 removed
`import_server()`. Tags can filter which components are included.

```python
from fastmcp import FastMCP
from fastmcp.server.transforms import Namespace

main = FastMCP("Main")
api_server = FastMCP("API")

@api_server.tool(tags=["public"])
def public_api(): ...

@api_server.tool(tags=["admin"])
def admin_api(): ...

# Mount with namespace (`prefix=` was removed in v4)
main.mount(api_server, namespace="api")  # Tools become: api_public_api
# main.mount(api_server, namespace="api", exclude_tags=["admin"])  # Filter

# Provider-level transforms via the returned mount reference
mount_ref = main.mount(api_server, namespace="api")
# mount_ref.add_transform(ToolTransform({...}))
```

**Resource prefix format:** Path (default since v2.4.0): `resource://prefix/path`.
Set with `resource_prefix_format="path"`.

## Storage Backends

Powered by `py-key-value-aio` for caching and OAuth state. Default is in-memory
(ephemeral). v3 default OAuth storage is `FileTreeStore` (replaces `DiskStore` due
to CVE-2025-69872).

| Backend | Import path | Use case |
| --- | --- | --- |
| Memory (default) | `from key_value.aio.stores.memory import MemoryStore` | Dev, testing, single-process |
| File / FileTree | `from key_value.aio.stores.filetree import FileTreeStore` | Persistent, single instance (Mac/Windows default) |
| Redis | `from key_value.aio.stores.redis import RedisStore` | Distributed, production, multi-instance |
| Others | DynamoDB, MongoDB, Elasticsearch, Memcached, RocksDB, Valkey | See py-key-value-aio docs |

```python
from pathlib import Path
from key_value.aio.stores.filetree import (
    FileTreeStore,
    FileTreeV1KeySanitizationStrategy,
    FileTreeV1CollectionSanitizationStrategy,
)
from fastmcp.server.middleware.caching import ResponseCachingMiddleware

storage_dir = Path("/var/cache/fastmcp")
store = FileTreeStore(
    data_directory=storage_dir,
    key_sanitization_strategy=FileTreeV1KeySanitizationStrategy(storage_dir),
    collection_sanitization_strategy=FileTreeV1CollectionSanitizationStrategy(storage_dir),
)
middleware = ResponseCachingMiddleware(cache_storage=store)
```

> **Required:** Sanitization strategies are required for `FileTreeStore` — without
> them, URL-based OAuth client IDs (e.g. `https://claude.ai/oauth/...`) crash with
> `FileNotFoundError`. Choose strategies upfront; changing them is a breaking change.

## OAuth & Authentication

Authentication applies only to HTTP-based transports (`http` and `sse`). STDIO
inherits security from its local execution environment. Four patterns:

### Pattern 1: Token Verification

Validate external tokens; treat your server as a resource server.

```python
from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import JWTVerifier

auth = JWTVerifier(
    jwks_uri="https://auth.example.com/.well-known/jwks.json",
    issuer="https://auth.example.com",
    audience="my-server",
)
mcp = FastMCP("Server", auth=auth)
```

> v2.14.0 removed `BearerAuthProvider`; use `JWTVerifier` (or `TokenVerifier` for
> non-JWT validation without OAuth discovery metadata).

### Pattern 2: External Identity Providers (RemoteAuthProvider)

For providers supporting Dynamic Client Registration (DCR): Descope, WorkOS AuthKit,
Scalekit, etc. Use `RemoteAuthProvider`.

### Pattern 3: OAuth Proxy (production for traditional providers)

Bridge providers without DCR (GitHub, Google, Azure, AWS Cognito, Discord, Facebook,
Keycloak, OCI, Supabase, Hugging Face). Presents a DCR-compliant interface to MCP
clients while using your pre-registered credentials upstream.

```python
import os
from fastmcp import FastMCP
from fastmcp.server.auth import OAuthProxy
from fastmcp.server.auth.providers.jwt import JWTVerifier
from key_value.aio.stores.redis import RedisStore

token_verifier = JWTVerifier(
    jwks_uri="https://api.github.com/.well-known/jwks.json",
    issuer="https://api.github.com",
    audience="my-app",
)

auth = OAuthProxy(
    upstream_authorization_endpoint="https://github.com/login/oauth/authorize",
    upstream_token_endpoint="https://github.com/login/oauth/access_token",
    upstream_client_id=os.environ["GITHUB_CLIENT_ID"],
    upstream_client_secret=os.environ["GITHUB_CLIENT_SECRET"],
    token_verifier=token_verifier,
    base_url="https://your-server.com",
    client_storage=RedisStore(host=os.getenv("REDIS_HOST")),
    # enable_consent_screen=True,  # Critical: prevents confused deputy attacks
)
mcp = FastMCP("GitHub Auth", auth=auth)
```

Provider-specific helpers (e.g. `from fastmcp.server.auth.providers.github import
GitHubProvider`) simplify setup. For OIDC-supporting providers (Auth0, Google OIDC,
Azure AD), use `OIDCProxy` for automatic endpoint discovery.

### Pattern 4: Full OAuth Server

`OAuthProvider` — complete authorization server (user management, token issuance,
validation). Most complex; maximum control.

> **Auth security checklist:** consent screens (prevents confused deputy attacks),
> encrypted storage (`FileTreeStore`/`RedisStore`), JWT signing key
> (`secrets.token_urlsafe(32)` in env), PKCE support, RFC 7662 token introspection.

## Icons, API Integration, Cloud Deployment

**Icons:** Add to servers, tools, resources, prompts via `Icon(url, size=)`,
`Icon.from_file()`, or `Image.to_data_uri()`.

**API integration (3 patterns):**

1. Manual — `httpx.AsyncClient` with `base_url`/`headers`/`timeout`.
2. OpenAPI auto-gen — `OpenAPIProvider` (GET→Resources/Templates, POST/PUT/DELETE→Tools).
   Set timeout on the `httpx.AsyncClient`, not the provider (v3 change).
3. FastAPI conversion — `FastMCP.from_fastapi(app, httpx_client_kwargs)`.

**Cloud deployment requirements:**

- Module-level server named `mcp`, `server`, or `app`.
- PyPI dependencies only in `requirements.txt`.
- Public GitHub repo (or accessible).
- Environment variables for config (never hardcode secrets).

```python
# Correct: module-level export
mcp = FastMCP("server")

# Wrong: function-wrapped (too late for cloud)
def create_server():
    return FastMCP("server")
```

**Horizon** (Prefect) is the enterprise MCP gateway for running FastMCP servers in
production: SSO, tool-level RBAC, audit logs, observability, branch previews.

## Project Configuration (fastmcp.json)

Declarative config — portable, shareable, replaces complex CLI args:

```json
{
  "$schema": "https://gofastmcp.com/public/schemas/fastmcp.json/v1.json",
  "source": {
    "path": "server.py",
    "entrypoint": "mcp"
  },
  "environment": {
    "type": "uv",
    "python": ">=3.10",
    "dependencies": ["pandas", "numpy"]
  },
  "deployment": {
    "transport": "stdio",
    "log_level": "INFO"
  }
}
```

```bash
fastmcp run fastmcp.json   # or just `fastmcp run` if fastmcp.json is present
```

## Common Errors (With Solutions)

### Error 1: Missing Server Object

**Error:** `RuntimeError: No server object found at module level`
**Cause:** Server not exported at module level (cloud requirement).
**Fix:** `mcp = FastMCP("server")` at module level, not inside functions.

### Error 2: Async/Await Confusion

**Error:** `RuntimeError: no running event loop` or
`TypeError: object coroutine can't be used in 'await'`
**Cause:** Mixing sync/async incorrectly.
**Fix:** Use `async def` for tools with `await`; `def` for non-async code.

### Error 3: Context Not Injected

**Error:** `TypeError: missing 1 required positional argument: 'context'`
**Cause:** Missing `Context` type annotation.
**Fix:** `async def tool(ctx: Context)` — type hint required. Preferred v3 form:
`ctx: Context = CurrentContext()`.

### Error 4: Resource URI Syntax

**Error:** `ValueError: Invalid resource URI: missing scheme`
**Cause:** Resource URI missing scheme prefix.
**Fix:** Use `@mcp.resource("data://config")`, not `@mcp.resource("config")`.

### Error 5: Resource Template Parameter Mismatch

**Error:** `TypeError: get_user() missing 1 required positional argument`
**Cause:** Function parameter names don't match URI template.
**Fix:** `@mcp.resource("user://{user_id}/profile")` → `def get_user(user_id: str)` —
names must match exactly.

### Error 6: Pydantic Validation Error

**Error:** `ValidationError: value is not a valid integer`
**Cause:** Type hints don't match provided data.
**Fix:** Use Pydantic models: `class Params(BaseModel): query: str = Field(min_length=1)`.

### Error 7: Transport/Protocol Mismatch

**Error:** `ConnectionError: Server using different transport`
**Cause:** Client and server using incompatible transports.
**Fix:** Match transports — stdio: `mcp.run()` +
`{"command": "python", "args": ["server.py"]}`; HTTP:
`mcp.run(transport="http", port=8000)` + `{"url": "http://localhost:8000/mcp", "transport": "http"}`.

### Error 8: Constructor Kwargs Removed (v3)

**Error:** `TypeError: FastMCP() got an unexpected keyword argument 'host'`
**Cause:** Passing transport/host/port/etc. to the constructor (v2 style).
**Fix:** Pass to `run()` or `run_http_async()`:
`mcp.run(transport="http", host="0.0.0.0", port=8080)`. Use `on_duplicate=` instead
of `on_duplicate_tools=`/`on_duplicate_resources=`/`on_duplicate_prompts=`.

### Error 9: Component Methods Removed (v3)

**Error:** `AttributeError: ... has no attribute 'get_tools'` or
`NotImplementedError` from `tool.enable()`.
**Cause:** Using v2 component methods.
**Fix:** Use `list_tools()`/`list_resources()`/`list_prompts()` (return lists, not
dicts). Use `server.disable(names={"tool_name"}, components={"tool"})` /
`server.enable(...)` instead of `tool.enable()/disable()`.

### Error 10: State Calls Not Awaited (v3)

**Error:** `TypeError: object coroutine can't be used in 'await'` or state silently
not set.
**Cause:** `ctx.set_state()`/`ctx.get_state()` are async in v3.
**Fix:** `await ctx.set_state(key, value)` / `await ctx.get_state(key, default=None)`.
Values must be JSON-serializable unless `serializable=False`.

### Error 11: State Not Shared Across Mounts (v3)

**Error:** Child server's `ctx.get_state("user_id")` returns `None` even though
parent middleware set it.
**Cause:** Each `FastMCP` instance has its own isolated state store.
**Fix:** Pass the same `session_state_store` to both servers, or use
`serializable=False` for request-scoped state (always shared).

### Error 12: PromptMessage Import (v3)

**Error:** `ImportError: cannot import name 'PromptMessage'`
**Cause:** Using v2 `mcp.types.PromptMessage`.
**Fix:** `from fastmcp.prompts import Message`; `Message("Hello")` (role defaults to
`"user"`). v3 requires typed `Message` objects or plain strings — no more silent dict
coercion.

### Error 13: Decorator Returns Function (v3)

**Error:** `AttributeError: 'function' object has no attribute 'name'`
**Cause:** v3 decorators return the original function, not a component object.
**Fix:** Access component metadata via the server (`list_tools()`). FastMCP v4
removed the old decorator compatibility mode.

### Error 14: OpenAPI Timeout (v3)

**Error:** `TypeError: OpenAPIProvider() got an unexpected keyword argument 'timeout'`
**Cause:** `timeout` removed from `OpenAPIProvider` in v3.
**Fix:** Set timeout on the `httpx.AsyncClient`:
`OpenAPIProvider(client=httpx.AsyncClient(timeout=30.0))`.

### Error 15: BearerAuthProvider Removed (v2.14.0)

**Error:** `ImportError: cannot import name 'BearerAuthProvider' from 'fastmcp.auth'`
**Cause:** Module removed in v2.14.0.
**Fix:** `from fastmcp.server.auth.providers.jwt import JWTVerifier` for token
validation, or `OAuthProxy` for full OAuth flows.

### Error 16: Image Import Path Changed (v2.14.0)

**Error:** `ImportError: cannot import name 'Image' from 'fastmcp'`
**Cause:** `fastmcp.Image` top-level import removed.
**Fix:** `from fastmcp.utilities import Image`.

### Error 17: FileTreeStore Crashes on OAuth Client IDs

**Error:** `FileNotFoundError` when storing OAuth clients with URL-based IDs.
**Cause:** `FileTreeStore` without sanitization strategies uses URLs as filesystem
paths.
**Fix:** Pass `key_sanitization_strategy` and `collection_sanitization_strategy`
(see Storage Backends above).

### Error 18: Background Tasks Not Installed (v3)

**Error:** `ImportError` or `task=True` not recognized.
**Cause:** Background tasks are an optional extra in v3.
**Fix:** `pip install "fastmcp[tasks]"`. Tasks also require async functions —
`task=True` on a sync function raises `ValueError` at registration.

### Error 19: FastAPI Mount Path Doubling

**Error:** Client can't connect to `/mcp`, gets 404. ([GitHub #2961](https://github.com/PrefectHQ/fastmcp/issues/2961))
**Cause:** Mounting FastMCP at `/mcp` creates `/mcp/mcp`.
**Fix:** Mount at root `app.mount("/", mcp_app)` (endpoint becomes `/mcp`), or adjust
client config to `http://localhost:8000/mcp/mcp`.

### Error 20: Lifespan Not Passed to ASGI App

**Error:** `RuntimeError: Database connection never initialized`
**Cause:** FastMCP with FastAPI without passing lifespan.
**Fix:** Use `combine_lifespans(app_lifespan, mcp_app.lifespan)` when mounting into
FastAPI.

### Error 21: Port Already in Use

**Error:** `OSError: [Errno 48] Address already in use`
**Fix:** Use a different port `--port 8001`, or `lsof -ti:8000 | xargs kill -9`.

### Error 22: Schema Generation Failures

**Error:** `TypeError: Object of type 'ndarray' is not JSON serializable`
**Cause:** Unsupported type hints.
**Fix:** Return JSON-compatible types (`list[float]`) or convert:
`{"values": np_array.tolist()}`. Custom classes must be `dict` or Pydantic
`BaseModel` — FastMCP supports all Pydantic-compatible types.

### Error 23: JSON Serialization

**Error:** `TypeError: Object of type 'datetime' is not JSON serializable`
**Fix:** Convert: `datetime.now(timezone.utc).isoformat()`; `bytes` → `.decode('utf-8')`.

### Error 24: Circular Imports

**Error:** `ImportError: cannot import name 'X' from partially initialized module`
**Cause:** Circular dependency (common in cloud deployment).
**Fix:** Direct imports in `__init__.py` (`from .api_client import APIClient`) or lazy
imports inside functions.

### Error 25: Import-Time Execution

**Error:** `RuntimeError: Event loop is closed`
**Cause:** Creating async resources at module import time.
**Fix:** Lazy initialization — create a connection class with an async `connect()`
method, call it in the lifespan.

### Error 26: Deprecation Warnings

**Error:** `DeprecationWarning: 'mcp.settings' is deprecated`
**Cause:** Using old FastMCP v1 API.
**Fix:** Use `os.getenv("API_KEY")` instead of `mcp.settings.get("API_KEY")`.

### Error 27: Python Version Compatibility

**Error:** `DeprecationWarning: datetime.utcnow() is deprecated`
**Cause:** Using deprecated Python 3.12+ methods.
**Fix:** `datetime.now(timezone.utc)` instead of `datetime.utcnow()`.

### Error 28: Storage Not Configured (Production)

**Error:** OAuth tokens lost on restart; cache not persisting.
**Cause:** Using default memory storage in production.
**Fix:** Encrypted `FileTreeStore` (single instance) or `RedisStore` (multi-instance).

### Error 29: Middleware Execution Order

**Error:** `RuntimeError: Rate limit not checked before caching`
**Cause:** Incorrect middleware ordering (order matters!).
**Fix:** `ErrorHandlingMiddleware` → `TimingMiddleware` → `LoggingMiddleware` →
`RateLimitingMiddleware` → `ResponseCachingMiddleware`.

### Error 30: Removed `import_server()`

**Error:** Subserver changes not reflected, or unexpected tool namespacing.
**Cause:** Using the v3-only `import_server()` API.
**Fix:** Use `mount()` for dynamic, live links and runtime delegation. FastMCP v4
removed `import_server()`.

### Error 31: Host/Origin Guard After v3.4.3

**Error:** v3.4.3 introduced strict Host/Origin validation that rejected existing
ASGI/serverless/reverse-proxy traffic.
**Fix:** v3.4.4 relaxed defaults — upgrade to `>=3.4.4`. Strict validation remains
available as opt-in for explicit trusted hosts/origins.

### Error 32: SSRF Allow-List Bypass

**Error:** NAT64/6to4/Teredo/ISATAP transition addresses smuggling private IPv4.
**Fix:** Fixed in v3.4.3 — upgrade. Every IPv6 transition form now unwraps to its
embedded IPv4 and is checked against the same policy.

## Production Patterns

1. **Utils module** — single `utils.py` with a `Config` class,
   `format_success`/`format_error` helpers.
2. **Connection pooling** — singleton `httpx.AsyncClient` with `get_client()`.
3. **Retry with backoff** — `retry_with_backoff(func, max_retries=3,
   initial_delay=1.0, exponential_base=2.0)`.
4. **Time-based caching** — `TimeBasedCache(ttl=300)` with `.get()` / `.set()`;
   or `ResponseCachingMiddleware(cache_storage=store)`.

### Testing

- **Unit:** `pytest` + `create_test_client(test_server)` + `await client.call_tool()`.
- **Integration:** `Client("server.py")` + `list_tools()` + `call_tool()` +
  `list_resources()`.
- See [Testing docs](https://gofastmcp.com/servers/testing.md).

### Project Structure

- **Simple:** `server.py`, `requirements.txt`, `.env`, `README.md`.
- **Production:** `src/` (`server.py`, `utils.py`, `tools/`, `resources/`,
  `prompts/`), `tests/`, `pyproject.toml`, `fastmcp.json`.

## Client Config (Claude Desktop)

```json
{
  "mcpServers": {
    "my-server": {
      "url": "https://project.fastmcp.app/mcp",
      "transport": "http"
    }
  }
}
```

For stdio-only hosts connecting to remote servers, use the `fastmcp-remote` bridge
(v3.4.0+):

```json
{
  "mcpServers": {
    "remote-api": {
      "command": "uvx",
      "args": ["fastmcp-remote", "https://example.com/mcp"]
    }
  }
}
```

## Key Takeaways

1. Module-level server export (`mcp = FastMCP("server")`) for cloud deployment.
2. Decorators (`@mcp.tool`) return the original function in v3 — no parentheses needed.
3. Pass transport/host/port to `run()`, not the constructor (v3).
4. `list_tools()` / `list_resources()` return lists, not dicts (v3).
5. `await ctx.set_state()` / `await ctx.get_state()` are async (v3).
6. State is isolated per `FastMCP` instance — share `session_state_store` across mounts.
7. `fastmcp.prompts.Message` replaces `mcp.types.PromptMessage` (v3).
8. `FileTreeStore` replaces `DiskStore` as OAuth default (CVE-2025-69872).
9. Background tasks are an optional extra: `pip install "fastmcp[tasks]"`.
10. Persistent storage (`FileTreeStore`/`RedisStore`) for OAuth/caching in production.
11. Server lifespans run once per server instance (not per session).
12. Middleware order: errors → timing → logging → rate limiting → caching.
13. Composition: `mount(namespace="x")` (dynamic); v4 removed `import_server()`.
14. OAuth security: consent screens + encrypted storage + JWT signing + PKCE.
15. Comprehensive docstrings (LLMs read these!).
16. Environment variables for config (never hardcode secrets).
17. Pin versions within the tested minor line: `fastmcp>=4.1.8,<4.2`.
18. In MCP SDK v2 protocol models, use Python `snake_case` attributes while
    preserving camelCase in JSON wire payloads.

## References

- **Official docs:** <https://gofastmcp.com>
- **GitHub:** <https://github.com/PrefectHQ/fastmcp>
- **MCP spec:** <https://modelcontextprotocol.io>
- **Changelog:** <https://gofastmcp.com/changelog.md>
- **Migration (v2 → v3):** <https://gofastmcp.com/getting-started/upgrading/from-fastmcp-2.md>
- **Migration (v3 → v4):** <https://gofastmcp.com/getting-started/upgrading/from-fastmcp-3.md>
- **Context7:** `/prefecthq/fastmcp`
- **Releases policy:** <https://gofastmcp.com/development/releases.md>

### Package Versions

- `fastmcp>=4.1.8,<4.2` (project lock: `v4.1.8`, 2026-11-12)
- Python `>=3.10`
- Dependencies: `httpx`, `pydantic`, `py-key-value-aio`, `mcp` SDK
- Optional: `fastmcp[tasks]`, `py-key-value-aio[redis]`

### Related Skills

- `fastmcp-docs` — research the official FastMCP docs index (bundled `llms.txt`).
- `mcp-builder` — MCP server development guide (TypeScript & Python).

---

_Last updated: 2026-11-12. Runtime constraint aligned with the project lock.
The 4.1 upgrade notes reference the official releases above; verify newer
patch-specific behavior against its release notes._
