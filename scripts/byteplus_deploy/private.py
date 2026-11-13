"""Allowlisted provider settings and host-bound encrypted transfer."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import secrets
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .workspace import ROOT

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

PROVIDERS = {
    "BYTEPLUS_MODELARK_API_KEY",
    "BYTEPLUS_SEED_SPEECH_API_KEY",
    "BYTEPLUS_VOD_MEDIAKIT_API_KEY",
    "BYTEPLUS_CV_API_KEY",
    "BYTEPLUS_MODELARK_BASE_URL",
    "BYTEPLUS_SEED_AUDIO_BASE_URL",
    "BYTEPLUS_OMNIHUMAN_ENABLED",
    "BYTEPLUS_MODELARK_3D_ENABLED",
}
LIMITS = {
    "DAILY_BUDGET_USD",
    "RATE_LIMIT_RPM",
    "RATE_LIMIT_BURST",
    "PROVIDER_MAX_CONCURRENCY",
    "PRINCIPAL_MAX_CONCURRENCY",
    "ARTIFACT_TTL_SECONDS",
}


def validator() -> ModuleType:
    """Load the existing pure host validator without importing the MCP server."""
    path = ROOT / "deploy/terraform/byteplus/scripts/check_env.py"
    spec = importlib.util.spec_from_file_location("deployment_env_validator", path)
    if spec is None or spec.loader is None:
        raise ValueError("Host validator is unavailable.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PRODUCT_KEYS = frozenset(validator().PRODUCT_KEYS)


def configuration_digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def make_configuration(provider_file: Path, hostname: str) -> tuple[bytes, str]:
    """Generate one new deployment configuration; callers persist and reuse it."""
    import os

    checker = validator()
    original = checker.load_environment(provider_file, expected_uid=os.getuid())
    return make_configuration_values(original, hostname)


def make_configuration_values(original: dict[str, str], hostname: str) -> tuple[bytes, str]:
    """Create a first deployment from validated provider inputs, never rotate retained keys."""
    checker = validator()
    template = ROOT / "deploy/compose/.env.example"
    values: dict[str, str] = {}
    for line in template.read_text().splitlines():
        if line and line[0].isupper() and "=" in line:
            key, value = line.split("=", 1)
            if "ACCESS_KEY" not in key and "SECRET_KEY" not in key and "SESSION_TOKEN" not in key:
                values[key] = value
    values.update({key: value for key, value in original.items() if key in PROVIDERS | LIMITS})
    token = secrets.token_hex(32)
    values.update(
        MCP_PUBLIC_HOST=hostname,
        MCP_AUTH_MODE="api_key",
        MCP_API_KEY_SHA256=configuration_digest(token.encode()),
        REDIS_PASSWORD=secrets.token_hex(32),
        FASTMCP_TASKS_ENCRYPTION_KEY=secrets.token_hex(32),
        ARTIFACT_BACKEND="filesystem",
    )
    for key, value in values.items():
        if any(character in value for character in "\r\n$`#\"'\\"):
            raise ValueError(f"{key} must be a plain literal value.")
    checker.validate_environment(values)
    return "".join(f"{key}={value}\n" for key, value in values.items()).encode(), token


def replace_provider_keys(payload: bytes, keys: dict[str, str]) -> bytes:
    """Replace the complete product-key set while preserving every other literal line."""
    lines = payload.decode().splitlines(keepends=True)
    result = []
    for line in lines:
        key = line.partition("=")[0]
        if key not in PRODUCT_KEYS:
            result.append(line)
    if result and not result[-1].endswith("\n"):
        result[-1] += "\n"
    result.extend(f"{key}={keys.get(key, '')}\n" for key in sorted(PRODUCT_KEYS))
    return "".join(result).encode()


def encrypt_payload(payload: bytes, public_pem: bytes, instance_id: str) -> dict[str, str]:
    """Encrypt to the verified guest public key and bind ciphertext to its ID."""
    public_key = serialization.load_pem_public_key(public_pem)
    if not isinstance(public_key, rsa.RSAPublicKey) or public_key.key_size < 3072:
        raise ValueError("Guest transfer key must be RSA with at least 3072 bits.")
    symmetric_key = AESGCM.generate_key(bit_length=256)
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(symmetric_key).encrypt(nonce, payload, instance_id.encode())
    wrapped = public_key.encrypt(
        symmetric_key,
        padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    return {
        "wrapped_key": base64.b64encode(wrapped).decode(),
        "nonce": base64.b64encode(nonce).decode(),
        "ciphertext": base64.b64encode(ciphertext).decode(),
    }
