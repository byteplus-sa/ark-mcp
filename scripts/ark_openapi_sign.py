#!/usr/bin/env python3
"""Sign a ModelArk OpenAPI (service=ark, v2024-01-01) request and run it with curl.

Phase 0 probe helper for plans/PLAN_MODELARK_ASSET_LIBRARY.md. Implements the
BytePlus OpenAPI v4 HMAC-SHA256 signature (see
specs/SPEC_VOD_OPENAPI_PROVIDER_CONTRACT.md) with service ``ark``. Never prints
credentials.

Env: BYTEPLUS_MODELARK_ACCESS_KEY, BYTEPLUS_MODELARK_SECRET_KEY,
     optional BYTEPLUS_MODELARK_SESSION_TOKEN (STS/TSP temporary credentials).
Usage: python3 scripts/ark_openapi_sign.py <Action> '<json body>'
"""

import datetime
import hashlib
import hmac
import json
import os
import subprocess
import sys
from urllib.parse import quote

HOST = os.environ.get("ARK_OPENAPI_HOST", "ark.ap-southeast-1.byteplusapi.com")
REGION = os.environ.get("ARK_OPENAPI_REGION", "ap-southeast-1")
SERVICE, VERSION = "ark", "2024-01-01"


def _h(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def main() -> None:
    action, body = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "{}"
    body = json.dumps(json.loads(body), separators=(",", ":"))
    ak = os.environ["BYTEPLUS_MODELARK_ACCESS_KEY"]
    sk = os.environ["BYTEPLUS_MODELARK_SECRET_KEY"]
    token = os.environ.get("BYTEPLUS_MODELARK_SESSION_TOKEN", "")

    now = datetime.datetime.now(datetime.UTC)
    x_date, short = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(body.encode()).hexdigest()
    query = "&".join(
        f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}"
        for k, v in sorted({"Action": action, "Version": VERSION}.items())
    )
    ctype = "application/json"
    signed_headers = "content-type;host;x-content-sha256;x-date"
    canonical_headers = (
        f"content-type:{ctype}\nhost:{HOST}\nx-content-sha256:{payload_hash}\nx-date:{x_date}\n"
    )
    if token:
        signed_headers += ";x-security-token"
        canonical_headers += f"x-security-token:{token}\n"
    canonical_request = "\n".join(
        ["POST", "/", query, canonical_headers, signed_headers, payload_hash]
    )
    scope = f"{short}/{REGION}/{SERVICE}/request"
    string_to_sign = "\n".join(
        ["HMAC-SHA256", x_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest()]
    )
    key = _h(_h(_h(_h(sk.encode(), short), REGION), SERVICE), "request")
    signature = hmac.new(key, string_to_sign.encode(), hashlib.sha256).hexdigest()
    auth = f"HMAC-SHA256 Credential={ak}/{scope}, SignedHeaders={signed_headers}, Signature={signature}"

    cmd = [
        "curl",
        "-sS",
        "-X",
        "POST",
        f"https://{HOST}/?{query}",
        "-H",
        f"Content-Type: {ctype}",
        "-H",
        f"Host: {HOST}",
        "-H",
        f"X-Date: {x_date}",
        "-H",
        f"X-Content-Sha256: {payload_hash}",
        "-H",
        f"Authorization: {auth}",
    ]
    if token:
        cmd += ["-H", f"X-Security-Token: {token}"]
    cmd += ["--data-binary", body]
    out = subprocess.run(cmd, capture_output=True, text=True)
    print(out.stdout or out.stderr)


if __name__ == "__main__":
    main()
