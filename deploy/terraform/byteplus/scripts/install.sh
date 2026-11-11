#!/usr/bin/env bash
set -euo pipefail
umask 077

if ! grep -qx 'ID=ubuntu' /etc/os-release ||
   ! grep -qx 'VERSION_ID="24.04"' /etc/os-release ||
   [[ "$(dpkg --print-architecture)" != "amd64" ]]; then
    printf '%s\n' 'Unsupported image: use Ubuntu 24.04 AMD64.' >&2
    exit 1
fi

if [[ -f /var/lib/ark-mcp-installed ]]; then
    exit 0
fi

export DEBIAN_FRONTEND=noninteractive
apt-get -o Acquire::Retries=3 update
apt-get -o Acquire::Retries=3 install -y docker.io docker-compose-v2 python3 util-linux
docker compose version
systemctl stop docker.service docker.socket
install -d -m 0755 /srv/ark-mcp /etc/systemd/system/docker.service.d
install -m 0644 /opt/ark-mcp/docker-storage.conf /etc/systemd/system/docker.service.d/ark-storage.conf
systemctl daemon-reload
touch /var/lib/ark-mcp-installed
printf '%s\n' 'Host prepared. Mount the retained disk, install the private .env, then run /opt/ark-mcp/activate.sh.'
