# Docker Compose kit: organization self-hosting

Runs one Ark Seed MCP server for one organization: Caddy (TLS) in front of the
server, with Redis for background jobs. Only Caddy is exposed.

```bash
cp .env.example .env    # then fill in every REQUIRED value
docker compose up -d --build
```

| File | Purpose |
|---|---|
| `compose.yaml` | Services, networks, volumes, and the pinned security settings |
| `Caddyfile` | TLS, request size limit, streaming, and blocking `/metrics` and `/ready` from outside |
| `.env.example` | Everything an organization configures: hostname, identity provider, BytePlus keys, guardrails, secrets |

Full guide, including identity provider requirements, scope roles, sizing,
backups, and the security checklist: [docs/self-hosting.md](../../docs/self-hosting.md).
