#!/usr/bin/env bash
set -euo pipefail
umask 077

if [[ "$(id -u)" != "0" ]]; then
    printf '%s\n' 'Run this command with sudo.' >&2
    exit 1
fi
if ! mountpoint -q /srv/ark-mcp; then
    printf '%s\n' 'Refusing to start: persistent storage is not mounted at /srv/ark-mcp.' >&2
    exit 1
fi

python3 /opt/ark-mcp/check_env.py /srv/ark-mcp/config/.env
for directory in redis-data caddy-data caddy-config; do
    install -d -m 0750 "/srv/ark-mcp/$directory"
done
install -d -m 0750 -o 1001 -g 1001 /srv/ark-mcp/ark-state
docker compose --env-file /srv/ark-mcp/config/.env -f /opt/ark-mcp/compose.yaml config --quiet
systemctl enable docker.service ark-mcp.service
systemctl start docker.service
systemctl restart ark-mcp.service
printf '%s\n' 'Stack started. Verify HTTPS /health and unauthenticated POST /mcp returns 401.'
