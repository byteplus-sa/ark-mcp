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
apt-get -o Acquire::Retries=3 install -y docker.io docker-compose-v2 python3 python3-cryptography python3-yaml openssl util-linux
python3 - <<'PY'
import os
from pathlib import Path

import yaml

directory = Path("/opt/ark-mcp")
helpers = yaml.safe_load((directory / "helpers.yaml").read_text())
expected = {"check_env.py", "prepare_state.py", "receive_config.py", "update_image.py", "update_config.py", "generate_mcp_key.py"}
if not isinstance(helpers, dict) or set(helpers) != expected or not all(isinstance(value, str) for value in helpers.values()):
    raise ValueError("Invalid helper archive")
for name, content in helpers.items():
    descriptor = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o700)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
PY
python3 -c 'import cryptography, yaml'
openssl version
docker compose version
systemctl stop docker.service docker.socket
install -d -m 0755 /srv/ark-mcp /etc/systemd/system/docker.service.d
install -m 0644 /opt/ark-mcp/docker-storage.conf /etc/systemd/system/docker.service.d/ark-storage.conf
systemctl daemon-reload
touch /var/lib/ark-mcp-installed
printf '%s\n' 'Host prepared. Mount the retained disk, install the private .env, then run /opt/ark-mcp/activate.sh.'
