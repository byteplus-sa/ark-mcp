# Transports

The server supports stdio and FastMCP Streamable HTTP.

## stdio

stdio is the secure local default. MCP JSON-RPC uses stdin/stdout; structured
logs use stderr.

```bash
make start
# equivalent
uv run python -m ark_mcp
```

Use this mode when an MCP client launches the server as a subprocess. The
local principal owns artifacts and Seedance tasks created by that process.

## Streamable HTTP

Loopback development can use local auth:

```bash
MCP_TRANSPORT=http MCP_HOST=127.0.0.1 uv run python -m ark_mcp
```

### Shared loopback server for multiple local clients

`stdio` starts one server process per client, and background job state lives in
that process. To let several local MCP clients — for example multiple opencode
agents — submit and poll the same `ark_job_*` jobs, run **one** shared loopback
server in local auth mode and point every client at it:

```bash
make shared-http
# equivalent
MCP_AUTH_MODE=local MCP_TRANSPORT=http MCP_HOST=127.0.0.1 MCP_PORT=3000 uv run python -m ark_mcp
```

All loopback clients resolve to the same `local`/`local` principal, share the
single in-process Docket queue, and share the `runtime.sqlite3` ownership store,
so any client can poll a job submitted by any other client. An opencode remote
client entry looks like:

```json
{
  "mcp": {
    "ark-mcp": {
      "type": "remote",
      "url": "http://127.0.0.1:3000/mcp",
      "oauth": false,
      "timeout": 600000
    }
  }
}
```

Caveats:

- The default in-memory Docket backend is process-local: restarting the shared
  server discards active and retained jobs. Set `FASTMCP_DOCKET_URL=redis://...`
  and run a worker for restart durability (single replica only).
- Keep `RATE_LIMIT_RPM=0` (the default): every loopback client shares one
  client-IP bucket, so enabling it applies an aggregate cap.
- Set an absolute `ARTIFACT_DIR` so the state database does not depend on the
  server's working directory.

Network deployment must use JWT verification:

```bash
MCP_TRANSPORT=http \
MCP_HOST=0.0.0.0 \
MCP_AUTH_MODE=jwt \
MCP_JWT_JWKS_URI=https://id.example.com/.well-known/jwks.json \
MCP_JWT_ISSUER=https://id.example.com/ \
MCP_JWT_AUDIENCE=ark-mcp \
MCP_ALLOWED_HOSTS=mcp.example.com \
MCP_ALLOWED_ORIGINS=https://client.example.com \
uv run python -m ark_mcp
```

The server validates JWT signature, issuer, audience, scopes, principal, and
tenant. Host/Origin protection and a streamed body-size limit are enabled.
Terminate TLS at a trusted reverse proxy and pass the original Host header.

## Operational HTTP routes

| Route | Authentication | Meaning |
|---|---|---|
| `/health` | none | Process liveness |
| `/ready` | none | Runtime, database, and artifact-directory readiness. When `READINESS_CHECK_PROVIDERS=true`, also checks provider connectivity and includes a `providers` field; returns 503 `"degraded"` if any provider is unreachable |
| `/metrics` | none | Prometheus exposition |

Restrict `/metrics` at the network or reverse-proxy layer if metric labels or
traffic volumes are operationally sensitive. MCP traffic remains on FastMCP's
configured Streamable HTTP path.

## MCP Inspector

```bash
make inspect
# or with reload
make inspect-dev
```

For authenticated HTTP inspection, configure the inspector/client to send a
Bearer token with the scopes needed by the tools being tested.
