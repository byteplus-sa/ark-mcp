# Deployment

Remote deployment uses FastMCP Streamable HTTP with fail-closed JWT
authentication. Local stdio remains the simplest operating mode.

For a single-organization deployment, see [Self-Hosting](self-hosting.md)
and the [Docker Compose kit](../deploy/compose/README.md). The kit includes
TLS ingress, an authenticated internal Redis job backend, and hardened
application settings. Its guide identifies remaining live-deployment checks.

For a published build, use a versioned `ghcr.io/byteplus-sa/ark-mcp:vX.Y.Z`
image or pin the digest from its GitHub Release. The registry's `latest`
tracks releases, while `edge` tracks passing main builds. See
[Versioning and releases](releases.md) for artifacts, approval, and rollback.

## Build the container

```bash
docker build --tag ark-mcp:latest .
```

The image uses pinned Python and uv versions, installs the locked production
environment, runs as UID/GID 1001, and probes `/health` with Python's standard
library. The image intentionally defaults to JWT mode; verifier settings must
be supplied at runtime.

## Run one protected instance

```bash
docker run --detach \
  --name ark-mcp \
  --publish 127.0.0.1:3000:3000 \
  --env BYTEPLUS_MODELARK_API_KEY \
  --env BYTEPLUS_SEED_SPEECH_API_KEY \
  --env MCP_JWT_JWKS_URI=https://id.example.com/.well-known/jwks.json \
  --env MCP_JWT_ISSUER=https://id.example.com/ \
  --env MCP_JWT_AUDIENCE=ark-mcp \
  --env MCP_ALLOWED_HOSTS=mcp.example.com,127.0.0.1 \
  --env MCP_ALLOWED_ORIGINS=https://client.example.com \
  --volume ark-mcp-artifacts:/app/.artifacts \
  ark-mcp:latest
```

Place a TLS-terminating reverse proxy in front of the loopback-published port.
Disable proxy buffering for Streamable HTTP/SSE and set read/send timeouts at
least as high as `BYTEPLUS_REQUEST_TIMEOUT_MS`. Preserve the public Host header
so the server allowlist can validate it.

## Probes and monitoring

```bash
curl --fail http://127.0.0.1:3000/health
curl --fail http://127.0.0.1:3000/ready
curl --fail http://127.0.0.1:3000/metrics
```

- `/health` reports process liveness without contacting providers.
- `/ready` checks the runtime-owned SQLite state, artifact directory, and (when
  `READINESS_CHECK_PROVIDERS=true`) provider connectivity.
- `/metrics` exports tool/provider request counts, duration, retries, artifact
  writes, and budget rejections for Prometheus.

Provider calls also create FastMCP/OpenTelemetry child spans when tracing is
configured. Application logs are structured JSON on stderr and redact secrets.

## Kubernetes shape

Use one replica with a persistent volume until distributed state adapters are
implemented. The important container settings are:

```yaml
containers:
  - name: ark-mcp
    image: ark-mcp:latest
    ports:
      - name: http
        containerPort: 3000
    env:
      - {name: MCP_TRANSPORT, value: http}
      - {name: MCP_HOST, value: 0.0.0.0}
      - {name: MCP_AUTH_MODE, value: jwt}
      - {name: MCP_JWT_JWKS_URI, value: "https://id.example.com/.well-known/jwks.json"}
      - {name: MCP_JWT_ISSUER, value: "https://id.example.com/"}
      - {name: MCP_JWT_AUDIENCE, value: ark-mcp}
      - {name: MCP_ALLOWED_HOSTS, value: mcp.example.com}
      - {name: MCP_ALLOWED_ORIGINS, value: "https://client.example.com"}
    readinessProbe:
      httpGet: {path: /ready, port: http}
    livenessProbe:
      httpGet: {path: /health, port: http}
    securityContext:
      allowPrivilegeEscalation: false
      readOnlyRootFilesystem: true
      runAsNonRoot: true
      runAsUser: 1001
    volumeMounts:
      - {name: artifacts, mountPath: /app/.artifacts}
```

Mount `/app/.artifacts` writable even with a read-only root filesystem. Load
provider credentials from a Secret rather than literal manifest values.

## State, artifacts, and shutdown

- **HTTP is stateless.** `python -m ark_mcp` serves Streamable HTTP with
  `stateless_http=True` (`__main__.py`); long-running work is tracked by job
  IDs, not MCP sessions (see
  [SPEC_BACKGROUND_JOBS.md](../specs/SPEC_BACKGROUND_JOBS.md)).
- **Shutdown waits for in-flight requests** up to `BYTEPLUS_REQUEST_TIMEOUT_MS`
  (uvicorn `timeout_graceful_shutdown`). Set the orchestrator's termination
  grace period at least that long.
- **SQLite is the only state backend.** `STATE_BACKEND=sqlite` is the only
  accepted value, and startup logs `single_instance_state_backend` as a
  reminder to run one replica (`runtime.py::build_state_stores`).
- **Artifacts can live off the pod.** `ARTIFACT_BACKEND=object_storage` stores
  durable artifacts in the configured TOS or S3 bucket; startup fails without
  `TOS_*` or `S3_*` credentials. **The sweeper does not delete objects there**,
  so configure a bucket lifecycle rule that matches `ARTIFACT_TTL_SECONDS`.
- **A background sweeper** runs every `ARTIFACT_SWEEP_INTERVAL_SECONDS`
  (default `3600`). It deletes expired filesystem artifacts and cache rows,
  and prunes ownership, object-key and budget rows older than
  `STATE_PRUNE_MAX_AGE_DAYS` (default `30`).

## Security checklist

- Use HTTPS and JWT mode for every non-loopback bind.
- Pin issuer, audience, and JWKS URI; issue least-privilege tool scopes.
- Set explicit Host and Origin allowlists.
- Set `RATE_LIMIT_RPM` to cap per-client-IP request frequency when exposed
  to untrusted clients. Behind a reverse proxy, set
  `RATE_LIMIT_TRUST_PROXY_HEADERS=true` only if the proxy overwrites
  `X-Forwarded-For`; otherwise every client shares the proxy's bucket.
- Keep the HTTP body limit and provider URL SSRF checks enabled.
- Mount the artifact directory with least privilege and protect backups.
- Restrict operational endpoints at the proxy/network layer as appropriate.
- Run one replica; filesystem/SQLite state and in-process limiters are not a
  distributed coordination mechanism.
- Keep `uv.lock`, base images, and GitHub Actions pins current through review.

See [Configuration](configuration.md), [Transports](transports.md), and
[Troubleshooting](troubleshooting.md) for the complete operating contract.


## Background tasks in a single-replica deployment

**Redis retention does not enable multiple replicas.** Provider budgets,
ownership, execution claims, artifact state and concurrency controls still use
single-instance services. Keep `runtime.sqlite3` on durable storage when using
`FASTMCP_DOCKET_URL=redis://...` or `rediss://...`, and preserve it with the queue.

JWT-authenticated Redis task deployments must set
`FASTMCP_TASKS_ENCRYPTION_KEY` through the secret manager. Startup rejects an
unencrypted backend. Use at least 32 random characters, the same key for all
retained snapshots, TLS for the connection, and restricted Redis access.
Snapshots carry caller credentials; task arguments/results may also contain
sensitive media and require backend-level protection.

After worker loss, an already-started required task fails on redelivery instead
of replaying paid provider work. Reconcile the saved provider ID where available;
if the submission result was lost, investigate provider-side status before
creating another operation. Optional output-retrieval tasks remain repeatable.
Deleting the runtime database or retaining queue state beyond execution-history
retention invalidates this recovery contract. No automatic exactly-once provider
submission guarantee is made.

Offline regression coverage includes encrypted snapshot round trips, wrong-key
failure, and a simulated interrupted provider operation across a reopened runtime
database. Live Redis failover and named desktop-client task support require
validation in the target environment; the supported protocol test uses FastMCP
4.0.3 over a real stdio subprocess with a mocked provider.

## Single shared loopback server (local multi-client)

When several local MCP clients must share background jobs, run one loopback
server in local auth mode instead of one `stdio` process per client:

```bash
make shared-http
```

Every loopback client is the same `local`/`local` principal and shares the
in-process Docket queue and `runtime.sqlite3`, so any client can poll a job
submitted by another. This does not change the single-replica contract: the
in-memory queue is lost on restart, budgets/ownership/execution claims remain
SQLite-backed, and Redis retention stays single-replica. See
[transports.md](transports.md) for the client configuration and caveats.
