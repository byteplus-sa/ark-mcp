"""Environment configuration for the Ark Seed MCP server.

Loads custom environment variables (provider credentials, model bindings,
artifact storage, transport settings) via Pydantic Settings. FastMCP's own
settings (log level, etc.) are handled separately by FastMCP.

Provider credentials are startup configuration only and never tool arguments.
If a credential is absent, the server must not register that product's tool set.
"""

from __future__ import annotations

import os
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import (
    AliasChoices,
    BaseModel,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


def resolve_env_file() -> str:
    """Return the env file to load, honoring an explicit absolute override.

    ``env_file`` is resolved by pydantic-settings relative to the process
    working directory. An MCP client that spawns this server from another
    directory therefore finds no ``.env``, and the server starts with no
    provider credentials and no provider tools. ``ARK_MCP_ENV_FILE`` lets a
    client point at the file explicitly instead of relying on the cwd.
    """
    override = os.environ.get("ARK_MCP_ENV_FILE", "").strip()
    return override or ".env"


def _warn_if_http_body_limit_blocks_media_upload(body_bytes: int) -> None:
    """Warn when the HTTP body limit cannot carry the largest media upload.

    ``MediaLimits.video_max_bytes`` (200 MiB) is the largest Base64 media the
    upload tool accepts, and Base64 inflates ~4/3x inside the JSON-RPC envelope.
    A body limit below that will 413 on inlined large uploads, so we warn — but
    do not fail — because an operator may intentionally keep a small body cap
    and route large media through ``media_upload`` (presign) or a file path.
    """
    from ark_mcp.observability.logger import warning as log_warning
    from ark_mcp.security.media_policy import MediaLimits

    video_max_bytes = MediaLimits().video_max_bytes
    inflated = (video_max_bytes * 4) // 3
    if body_bytes < inflated:
        log_warning(
            "http_body_limit_below_media_upload_max",
            mcp_http_max_body_bytes=body_bytes,
            media_video_max_bytes=video_max_bytes,
            note=(
                "Inlined Base64 media uploads larger than this body limit will be "
                "rejected with HTTP 413; use the presign/direct-upload path for large media."
            ),
        )


class SeedreamFamily(StrEnum):
    PRO = "pro"
    LITE = "lite"
    V4X = "4x"


class SeedanceFamily(StrEnum):
    STANDARD = "standard"
    FAST = "fast"
    MINI = "mini"
    SEEDANCE_2_5 = "seedance_2_5"


class SeedUnderstandingFamily(StrEnum):
    PRO = "pro"
    TURBO = "turbo"


class Seed3DFamily(StrEnum):
    HYPER3D = "hyper3d"
    HITEM3D = "hitem3d"


class AuthMode(StrEnum):
    LOCAL = "local"
    JWT = "jwt"


class ImageModelBinding(BaseModel):
    model_id: str = Field(min_length=1)
    family: SeedreamFamily


class VideoModelBinding(BaseModel):
    model_id: str = Field(min_length=1)
    family: SeedanceFamily


class UnderstandingModelBinding(BaseModel):
    model_id: str = Field(min_length=1)
    family: SeedUnderstandingFamily


class Seed3DModelBinding(BaseModel):
    model_id: str = Field(min_length=1)
    family: Seed3DFamily


class Settings(BaseSettings):
    """Application settings loaded from environment / .env file.

    Uses ``validation_alias`` (not ``alias``) for environment variable name
    overrides so that field names are preserved for serialization and code
    access. See https://pydantic.dev/docs/validation/latest/concepts/pydantic_settings/.

    ``secrets_dir`` points to ``/run/secrets`` (Docker / Kubernetes) and
    emits a warning when the directory does not exist. Individual fields can
    also be overridden by placing a file named after the field (or its
    validation alias) in the secrets directory.
    """

    model_config = SettingsConfigDict(
        env_file=resolve_env_file(),
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        validate_default=True,
        secrets_dir="/run/secrets" if Path("/run/secrets").is_dir() else None,
    )

    # --- Provider credentials ------------------------------------------------

    modelark_api_key: str = Field(default="", validation_alias="BYTEPLUS_MODELARK_API_KEY")
    seed_speech_api_key: str = Field(
        default="",
        validation_alias="BYTEPLUS_SEED_SPEECH_API_KEY",
        description="Seed Speech API key for both Seed Audio (TTS) and ASR (speech-to-text).",
    )
    vod_mediakit_api_key: str = Field(
        default="",
        validation_alias="BYTEPLUS_VOD_MEDIAKIT_API_KEY",
        description="BytePlus VOD AI MediaKit Bearer API key.",
    )

    # --- Provider base URLs --------------------------------------------------

    modelark_base_url: str = Field(
        default="https://ark.ap-southeast.bytepluses.com/api/v3",
        validation_alias="BYTEPLUS_MODELARK_BASE_URL",
    )
    seed_audio_base_url: str = Field(
        default="https://voice.ap-southeast-1.bytepluses.com",
        validation_alias="BYTEPLUS_SEED_AUDIO_BASE_URL",
    )
    vod_mediakit_base_url: str = Field(
        default="https://mediakit.ap-southeast-1.bytepluses.com/api/v1",
        validation_alias="BYTEPLUS_VOD_MEDIAKIT_BASE_URL",
        description="BytePlus VOD AI MediaKit HTTPS API base URL.",
    )

    # --- Seed Speech ASR (STT) configuration ---------------------------------

    seed_speech_asr_base_url: str = Field(
        default="https://voice.ap-southeast-1.bytepluses.com",
        validation_alias="SEED_SPEECH_ASR_BASE_URL",
        description="Seed Speech ASR HTTP base URL.",
    )
    seed_speech_asr_poll_interval_seconds: float = Field(
        default=3.0,
        ge=0.5,
        le=60.0,
        validation_alias="SEED_SPEECH_ASR_POLL_INTERVAL_SECONDS",
        description="Seconds between ASR query polls.",
    )
    seed_speech_asr_poll_max_seconds: float = Field(
        default=600.0,
        ge=10.0,
        le=3600.0,
        validation_alias="SEED_SPEECH_ASR_POLL_MAX_SECONDS",
        description="Maximum total seconds to wait for ASR result.",
    )

    # --- Model bindings ------------------------------------------------------

    seedream_prompt_optimization_mode: Literal["standard", "fast"] = Field(
        default="standard",
        validation_alias="SEEDREAM_PROMPT_OPTIMIZATION_MODE",
        description=(
            "Prompt optimization mode Seedream 5.0 Pro uses when a caller omits "
            "prompt_optimization: 'standard' for prompt-following quality, 'fast' for "
            "lower latency. Both are accepted by the provider. An explicit per-call "
            "value always wins, and other Seedream families send no mode regardless."
        ),
    )
    seedream_default_model: str = Field(
        default="dola-seedream-5-0-pro-260628",
        validation_alias="SEEDREAM_DEFAULT_MODEL",
    )
    seedance_default_model: str = Field(
        default="dreamina-seedance-2-0-260128",
        validation_alias="SEEDANCE_DEFAULT_MODEL",
    )
    seedream_model_family: str = Field(
        default="",
        validation_alias="SEEDREAM_MODEL_FAMILY",
        description="Explicit family: 'pro', 'lite', or '4x'. Empty = infer from model ID.",
    )
    seedance_model_family: str = Field(
        default="",
        validation_alias="SEEDANCE_MODEL_FAMILY",
        description="Explicit family: 'standard', 'fast', 'mini', or 'seedance_2_5'. Empty = infer.",
    )
    seedream_model_bindings: list[ImageModelBinding] = Field(
        default_factory=list,
        validation_alias="SEEDREAM_MODEL_BINDINGS",
    )
    seedance_model_bindings: list[VideoModelBinding] = Field(
        default_factory=list,
        validation_alias="SEEDANCE_MODEL_BINDINGS",
    )
    seed_understanding_default_model: str = Field(
        default="dola-seed-2-1-turbo-260628",
        validation_alias="SEED_UNDERSTANDING_DEFAULT_MODEL",
    )
    seed_understanding_model_family: str = Field(
        default="",
        validation_alias="SEED_UNDERSTANDING_MODEL_FAMILY",
        description="Explicit family: 'pro' or 'turbo'. Empty = infer from default model ID.",
    )
    seed_understanding_model_bindings: list[UnderstandingModelBinding] = Field(
        default_factory=list,
        validation_alias="SEED_UNDERSTANDING_MODEL_BINDINGS",
    )

    # --- 3D generation feature flag and model bindings ----------------------

    seed3d_enabled: bool = Field(
        default=False,
        validation_alias="BYTEPLUS_MODELARK_3D_ENABLED",
        description=(
            "Feature flag for ModelArk 3D generation (Hyper3D + Hitem3d). "
            "Disabled by default. When false, no 3D tools are registered even "
            "if BYTEPLUS_MODELARK_API_KEY is configured."
        ),
    )
    hyper3d_default_model: str = Field(
        default="hyper3d-gen2-260112",
        validation_alias="HYPER3D_DEFAULT_MODEL",
    )
    hitem3d_default_model: str = Field(
        default="hitem3d-2-0-251223",
        validation_alias="HITEM3D_DEFAULT_MODEL",
    )
    seed3d_model_bindings: list[Seed3DModelBinding] = Field(
        default_factory=list,
        validation_alias="SEED3D_MODEL_BINDINGS",
    )

    # --- MCP transport -------------------------------------------------------

    mcp_transport: str = Field(
        default="stdio",
        validation_alias=AliasChoices("MCP_TRANSPORT", "FASTMCP_TRANSPORT"),
    )
    mcp_host: str = Field(
        default="127.0.0.1",
        validation_alias=AliasChoices("MCP_HOST", "FASTMCP_HOST"),
    )
    mcp_port: int = Field(
        default=3000,
        ge=1,
        le=65535,
        validation_alias=AliasChoices("MCP_PORT", "FASTMCP_PORT"),
    )
    mcp_allowed_origins: str = Field(default="", validation_alias="MCP_ALLOWED_ORIGINS")
    mcp_allowed_hosts: str = Field(
        default="127.0.0.1,localhost,[::1]",
        validation_alias="MCP_ALLOWED_HOSTS",
    )
    mcp_auth_mode: AuthMode = Field(default=AuthMode.LOCAL, validation_alias="MCP_AUTH_MODE")
    mcp_jwt_jwks_uri: str | None = Field(default=None, validation_alias="MCP_JWT_JWKS_URI")
    mcp_jwt_issuer: str | None = Field(default=None, validation_alias="MCP_JWT_ISSUER")
    mcp_jwt_audience: str | None = Field(default=None, validation_alias="MCP_JWT_AUDIENCE")
    mcp_tenant_claim: str = Field(
        default="tenant_id",
        min_length=1,
        validation_alias="MCP_TENANT_CLAIM",
    )
    mcp_jwt_clock_skew_seconds: int = Field(
        default=30,
        ge=0,
        le=300,
        validation_alias="MCP_JWT_CLOCK_SKEW_SECONDS",
        description="Tolerated clock skew (seconds) when validating JWT nbf (not-before) claims.",
    )
    mcp_jwt_provide_discovery: bool = Field(
        default=False,
        validation_alias="MCP_JWT_PROVIDE_DISCOVERY",
        description=(
            "When true and MCP_AUTH_MODE=jwt, serve RFC 9728 OAuth Protected Resource "
            "Metadata so spec-compliant MCP clients can discover the authorization server."
        ),
    )
    mcp_public_base_url: str = Field(
        default="",
        validation_alias="MCP_PUBLIC_BASE_URL",
        description=(
            "Public HTTPS base URL of this server, required when MCP_JWT_PROVIDE_DISCOVERY "
            "is enabled (e.g. 'https://mcp.example.com')."
        ),
    )
    mcp_jwt_scopes_supported: str = Field(
        default="",
        validation_alias="MCP_JWT_SCOPES_SUPPORTED",
        description="Comma-separated scopes to advertise in Protected Resource Metadata.",
    )
    readiness_check_providers: bool = Field(
        default=False,
        validation_alias="READINESS_CHECK_PROVIDERS",
        description=(
            "When true, the /ready endpoint also checks provider connectivity. "
            "Increases probe latency by up to READINESS_PROVIDER_TIMEOUT_SECONDS "
            "per configured provider."
        ),
    )
    readiness_provider_timeout_seconds: float = Field(
        default=2.0,
        ge=0.5,
        le=10.0,
        validation_alias="READINESS_PROVIDER_TIMEOUT_SECONDS",
        description="Per-provider timeout for readiness connectivity checks.",
    )
    rate_limit_rpm: int = Field(
        default=0,
        ge=0,
        validation_alias="RATE_LIMIT_RPM",
        description="Maximum HTTP requests per minute per client IP. 0 disables rate limiting.",
    )
    rate_limit_burst: int = Field(
        default=0,
        ge=0,
        validation_alias="RATE_LIMIT_BURST",
        description="Maximum burst size for the token bucket. 0 defaults to RATE_LIMIT_RPM.",
    )
    rate_limit_trust_proxy_headers: bool = Field(
        default=False,
        validation_alias="RATE_LIMIT_TRUST_PROXY_HEADERS",
        description=(
            "Trust the first X-Forwarded-For entry for rate-limit keys instead of the "
            "socket peer IP. Only enable behind a trusted proxy that overwrites the header."
        ),
    )

    # --- Artifact persistence ------------------------------------------------

    artifact_backend: Literal["filesystem", "object_storage"] = Field(
        default="filesystem",
        validation_alias="ARTIFACT_BACKEND",
        description=(
            "Durable artifact storage backend. 'filesystem' stores artifacts on "
            "local disk; 'object_storage' stores them in the configured TOS/S3 bucket."
        ),
    )
    state_backend: Literal["sqlite"] = Field(
        default="sqlite",
        validation_alias="STATE_BACKEND",
        description=(
            "Backend for task ownership, budget, and artifact-cache state. "
            "Only 'sqlite' (single instance) is implemented today."
        ),
    )
    artifact_sweep_interval_seconds: int = Field(
        default=3600,
        ge=60,
        validation_alias="ARTIFACT_SWEEP_INTERVAL_SECONDS",
        description="Interval in seconds between background artifact/state expiry sweeps.",
    )
    state_prune_max_age_days: int = Field(
        default=30,
        ge=1,
        validation_alias="STATE_PRUNE_MAX_AGE_DAYS",
        description="Maximum age in days for task-ownership, budget, and cache rows before pruning.",
    )
    artifact_dir: str = Field(default="~/.ark-mcp/artifacts", validation_alias="ARTIFACT_DIR")
    artifact_ttl_seconds: int = Field(default=604800, validation_alias="ARTIFACT_TTL_SECONDS")
    mcp_inline_media_max_bytes: int = Field(
        default=8388608, validation_alias="MCP_INLINE_MEDIA_MAX_BYTES"
    )
    artifact_download_timeout_seconds: float = Field(
        default=120.0,
        gt=0,
        validation_alias="ARTIFACT_DOWNLOAD_TIMEOUT_SECONDS",
        description=(
            "Timeout in seconds for each attempt to download provider output into "
            "artifact storage (both filesystem and object-storage backends)."
        ),
    )
    artifact_download_max_attempts: int = Field(
        default=3,
        ge=1,
        le=10,
        validation_alias="ARTIFACT_DOWNLOAD_MAX_ATTEMPTS",
        description=(
            "Maximum attempts for a retryable provider output download "
            "(timeouts, network errors, 408/429/5xx). Downloads are side-effect free."
        ),
    )
    artifact_inline_fallback_max_bytes: int = Field(
        default=8 * 1024 * 1024,
        ge=0,
        validation_alias="ARTIFACT_INLINE_FALLBACK_MAX_BYTES",
        description=(
            "When inline Base64 output (Seed Audio, Seedream b64_json) cannot be stored, "
            "return it inline up to this decoded size instead of dropping it. 0 disables."
        ),
    )
    output_roots: str = Field(
        default="",
        validation_alias="OUTPUT_ROOTS",
        description=(
            "Comma-separated absolute directories where stdio tools may write files "
            "(output_path, output_dir, save_to, destination_path). The client's MCP roots "
            "are used when available; when neither is set, path writing is disabled."
        ),
    )
    mcp_http_max_body_bytes: int = Field(
        default=300 * 1024 * 1024,
        ge=1,
        validation_alias="MCP_HTTP_MAX_BODY_BYTES",
        description=(
            "Maximum Streamable HTTP request body in bytes. Defaults large enough to "
            "inline the largest supported Base64 media upload (200 MiB video inflates "
            "~4/3x when Base64-encoded inside the JSON-RPC envelope)."
        ),
    )

    # --- TOS object storage -------------------------------------------------

    tos_access_key: str = Field(default="", validation_alias="TOS_ACCESS_KEY")
    tos_secret_key: str = Field(default="", validation_alias="TOS_SECRET_KEY")
    tos_security_token: str = Field(default="", validation_alias="TOS_SECURITY_TOKEN")
    tos_bucket: str = Field(default="", validation_alias="TOS_BUCKET")
    tos_region: str = Field(default="ap-southeast-1", validation_alias="TOS_REGION")
    tos_endpoint: str = Field(
        default="tos-ap-southeast-1.bytepluses.com",
        validation_alias="TOS_ENDPOINT",
    )
    tos_presign_ttl_seconds: int = Field(
        default=1800,
        ge=60,
        le=604800,
        validation_alias="TOS_PRESIGN_TTL_SECONDS",
    )

    # --- S3 object storage (Amazon S3 / S3-compatible) -----------------------

    s3_access_key: str = Field(default="", validation_alias="S3_ACCESS_KEY")
    s3_secret_key: str = Field(default="", validation_alias="S3_SECRET_KEY")
    s3_bucket: str = Field(default="", validation_alias="S3_BUCKET")
    s3_region: str = Field(default="us-east-1", validation_alias="S3_REGION")
    s3_endpoint: str = Field(default="", validation_alias="S3_ENDPOINT")
    s3_presign_ttl_seconds: int = Field(
        default=1800,
        ge=60,
        le=604800,
        validation_alias="S3_PRESIGN_TTL_SECONDS",
    )
    object_storage_backend: Literal["tos", "s3"] = Field(
        default="tos",
        validation_alias="OBJECT_STORAGE_BACKEND",
    )

    # --- HTTP timeouts (milliseconds) ---------------------------------------

    connect_timeout_ms: int = Field(default=10000, validation_alias="BYTEPLUS_CONNECT_TIMEOUT_MS")
    request_timeout_ms: int = Field(
        default=600000,
        validation_alias="BYTEPLUS_REQUEST_TIMEOUT_MS",
        description=(
            "10-minute request timeout covers long synchronous generations "
            "(Seedream Pro can take 60-150s, longer under cold start or queue)."
        ),
    )

    seed_understanding_timeout_ms: int | None = Field(
        default=None,
        gt=0,
        validation_alias="SEED_UNDERSTANDING_TIMEOUT_MS",
        description=(
            "Request timeout for seed_understand chat completions. Deep thinking is always "
            "on, so long analyses may need more than BYTEPLUS_REQUEST_TIMEOUT_MS. "
            "Defaults to BYTEPLUS_REQUEST_TIMEOUT_MS."
        ),
    )
    understanding_inline_max_chars: int = Field(
        default=20000,
        ge=1000,
        validation_alias="UNDERSTANDING_INLINE_MAX_CHARS",
        description=(
            "seed_understand answers longer than this are truncated inline; the full "
            "answer stays available through save_to."
        ),
    )
    media_upload_batch_max_bytes: int = Field(
        default=500 * 1024 * 1024,
        ge=1,
        validation_alias="MEDIA_UPLOAD_BATCH_MAX_BYTES",
        description="Maximum total decoded bytes accepted by one media_upload_batch call.",
    )

    # --- Runtime policy -----------------------------------------------------

    provider_max_concurrency: int = Field(
        default=5,
        ge=1,
        validation_alias="PROVIDER_MAX_CONCURRENCY",
    )
    principal_max_concurrency: int = Field(
        default=3,
        ge=1,
        validation_alias="PRINCIPAL_MAX_CONCURRENCY",
    )
    daily_budget_usd: float = Field(
        default=0.0,
        ge=0,
        validation_alias="DAILY_BUDGET_USD",
        description="Per-principal UTC daily limit. Zero records usage without blocking.",
    )
    persistence_cache_max_size: int = Field(
        default=10_000,
        ge=1,
        validation_alias="PERSISTENCE_CACHE_MAX_SIZE",
        description="Maximum number of provider task IDs cached in the artifact-resolution cache.",
    )
    persistence_cache_ttl_seconds: int = Field(
        default=86_400,
        ge=60,
        validation_alias="PERSISTENCE_CACHE_TTL_SECONDS",
        description=(
            "TTL in seconds for cached provider task to artifact mappings. "
            "Entries older than this are ignored on read."
        ),
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO",
        validation_alias="ARK_LOG_LEVEL",
    )

    # --- Convenience flags ---------------------------------------------------

    @property
    def has_modelark(self) -> bool:
        """Whether ModelArk credentials are configured (Seedream + Seedance)."""
        return bool(self.modelark_api_key)

    @property
    def has_understanding(self) -> bool:
        """Whether Seed 2.1 understanding is configured (reuses ModelArk key)."""
        return bool(self.modelark_api_key)

    @property
    def has_seed3d(self) -> bool:
        """Whether ModelArk 3D generation is enabled (separate flag, same key)."""
        return self.seed3d_enabled and bool(self.modelark_api_key)

    @property
    def has_seed_audio(self) -> bool:
        """Whether Seed Audio credentials are configured."""
        return bool(self.seed_speech_api_key)

    @property
    def has_vod_mediakit(self) -> bool:
        """Whether BytePlus VOD AI MediaKit credentials are configured."""
        return bool(self.vod_mediakit_api_key)

    @property
    def has_tos(self) -> bool:
        """Whether TOS object storage credentials are configured."""
        return bool(self.tos_access_key and self.tos_secret_key and self.tos_bucket)

    @property
    def has_s3(self) -> bool:
        """Whether S3 object storage credentials are configured."""
        return bool(self.s3_access_key and self.s3_secret_key and self.s3_bucket)

    @property
    def has_object_storage(self) -> bool:
        """Whether the *selected* object-storage backend is configured."""
        if self.object_storage_backend == "s3":
            return self.has_s3
        return self.has_tos

    @property
    def presign_ttl_seconds(self) -> int:
        """Presign TTL of the *selected* backend."""
        if self.object_storage_backend == "s3":
            return self.s3_presign_ttl_seconds
        return self.tos_presign_ttl_seconds

    @property
    def output_root_paths(self) -> list[str]:
        """Configured OUTPUT_ROOTS entries (unresolved, non-empty)."""
        return [item.strip() for item in self.output_roots.split(",") if item.strip()]

    @property
    def has_stt(self) -> bool:
        """Whether Seed Speech ASR (STT) is configured. Reuses the Seed Speech key."""
        return bool(self.seed_speech_api_key)

    @property
    def allowed_origins(self) -> list[str]:
        """Parse the comma-separated allowed origins list."""
        if not self.mcp_allowed_origins:
            return []
        return [origin.strip() for origin in self.mcp_allowed_origins.split(",") if origin.strip()]

    @property
    def allowed_hosts(self) -> list[str]:
        return [host.strip() for host in self.mcp_allowed_hosts.split(",") if host.strip()]

    @property
    def jwt_scopes_supported(self) -> list[str]:
        return [
            scope.strip() for scope in self.mcp_jwt_scopes_supported.split(",") if scope.strip()
        ]

    @field_validator("mcp_transport")
    @classmethod
    def validate_transport(cls, v: str) -> str:
        allowed = {"stdio", "http"}
        if v not in allowed:
            raise ValueError(f"MCP_TRANSPORT must be one of {allowed}, got '{v}'")
        return v

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator(
        "modelark_base_url",
        "seed_audio_base_url",
        "seed_speech_asr_base_url",
        "vod_mediakit_base_url",
    )
    @classmethod
    def validate_provider_url(cls, value: str, info: ValidationInfo) -> str:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname:
            env_var_map = {
                "modelark_base_url": "BYTEPLUS_MODELARK_BASE_URL",
                "seed_audio_base_url": "BYTEPLUS_SEED_AUDIO_BASE_URL",
                "seed_speech_asr_base_url": "SEED_SPEECH_ASR_BASE_URL",
                "vod_mediakit_base_url": "BYTEPLUS_VOD_MEDIAKIT_BASE_URL",
            }
            variable = env_var_map.get(info.field_name or "", "BYTEPLUS_PROVIDER_BASE_URL")
            raise ValueError(f"{variable} must use HTTPS and include a host")
        if parsed.username or parsed.password:
            raise ValueError("Provider base URLs must not contain credentials")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_model_bindings(self) -> Settings:
        """Resolve compatibility settings without model-ID string inference."""
        if not self.seedream_model_bindings:
            family = self.seedream_model_family.strip().lower()
            if not family:
                if self.seedream_default_model == "dola-seedream-5-0-pro-260628":
                    family = SeedreamFamily.PRO
                else:
                    raise ValueError(
                        "A custom SEEDREAM_DEFAULT_MODEL requires "
                        "SEEDREAM_MODEL_FAMILY or SEEDREAM_MODEL_BINDINGS."
                    )
            self.seedream_model_bindings = [
                ImageModelBinding(
                    model_id=self.seedream_default_model,
                    family=SeedreamFamily(family),
                )
            ]

        if not self.seedance_model_bindings:
            family = self.seedance_model_family.strip().lower()
            if not family:
                if self.seedance_default_model == "dreamina-seedance-2-0-260128":
                    family = SeedanceFamily.STANDARD
                elif self.seedance_default_model == "dreamina-seedance-2-5-260628":
                    family = SeedanceFamily.SEEDANCE_2_5
                else:
                    raise ValueError(
                        "A custom SEEDANCE_DEFAULT_MODEL requires "
                        "SEEDANCE_MODEL_FAMILY or SEEDANCE_MODEL_BINDINGS."
                    )
            self.seedance_model_bindings = [
                VideoModelBinding(
                    model_id=self.seedance_default_model,
                    family=SeedanceFamily(family),
                )
            ]

        if not self.seed_understanding_model_bindings:
            family = self.seed_understanding_model_family.strip().lower()
            if not family:
                if self.seed_understanding_default_model == "dola-seed-2-1-turbo-260628":
                    family = SeedUnderstandingFamily.TURBO
                elif self.seed_understanding_default_model == "dola-seed-evolving":
                    family = SeedUnderstandingFamily.PRO
                else:
                    raise ValueError(
                        "A custom SEED_UNDERSTANDING_DEFAULT_MODEL requires "
                        "SEED_UNDERSTANDING_MODEL_FAMILY or SEED_UNDERSTANDING_MODEL_BINDINGS."
                    )
            self.seed_understanding_model_bindings = [
                UnderstandingModelBinding(
                    model_id=self.seed_understanding_default_model,
                    family=SeedUnderstandingFamily(family),
                )
            ]

        if not self.seed3d_model_bindings:
            self.seed3d_model_bindings = [
                Seed3DModelBinding(
                    model_id=self.hyper3d_default_model,
                    family=Seed3DFamily.HYPER3D,
                ),
                Seed3DModelBinding(
                    model_id=self.hitem3d_default_model,
                    family=Seed3DFamily.HITEM3D,
                ),
            ]

        for label, bindings, default_model in (
            ("Seedream", self.seedream_model_bindings, self.seedream_default_model),
            ("Seedance", self.seedance_model_bindings, self.seedance_default_model),
            (
                "SeedUnderstanding",
                self.seed_understanding_model_bindings,
                self.seed_understanding_default_model,
            ),
            ("Seed3D", self.seed3d_model_bindings, self.hyper3d_default_model),
            ("Seed3D", self.seed3d_model_bindings, self.hitem3d_default_model),
        ):
            ids = [binding.model_id for binding in bindings]
            if len(ids) != len(set(ids)):
                raise ValueError(f"{label} model bindings contain duplicate IDs.")
            if default_model not in ids:
                raise ValueError(
                    f"{label} default model '{default_model}' is missing from its bindings."
                )
        if self.artifact_ttl_seconds <= 0:
            raise ValueError("ARTIFACT_TTL_SECONDS must be positive")
        if self.mcp_inline_media_max_bytes <= 0:
            raise ValueError("MCP_INLINE_MEDIA_MAX_BYTES must be positive")
        if self.mcp_http_max_body_bytes <= 0:
            raise ValueError("MCP_HTTP_MAX_BODY_BYTES must be positive")
        if self.mcp_transport == "http":
            _warn_if_http_body_limit_blocks_media_upload(self.mcp_http_max_body_bytes)
        if self.connect_timeout_ms <= 0 or self.request_timeout_ms <= 0:
            raise ValueError("Provider timeouts must be positive")
        for origin in self.allowed_origins:
            parsed = urlsplit(origin)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError(f"Invalid MCP allowed origin: '{origin}'")
        if self.mcp_auth_mode is AuthMode.JWT:
            missing = [
                name
                for name, value in (
                    ("MCP_JWT_JWKS_URI", self.mcp_jwt_jwks_uri),
                    ("MCP_JWT_ISSUER", self.mcp_jwt_issuer),
                    ("MCP_JWT_AUDIENCE", self.mcp_jwt_audience),
                )
                if not value
            ]
            if missing:
                raise ValueError(f"JWT auth requires: {', '.join(missing)}")
            parsed_jwks = urlsplit(self.mcp_jwt_jwks_uri or "")
            if parsed_jwks.scheme != "https" or not parsed_jwks.hostname:
                raise ValueError("MCP_JWT_JWKS_URI must be an HTTPS URL")
        if self.mcp_jwt_provide_discovery:
            if self.mcp_auth_mode is not AuthMode.JWT:
                raise ValueError("MCP_JWT_PROVIDE_DISCOVERY requires MCP_AUTH_MODE=jwt.")
            parsed_base = urlsplit(self.mcp_public_base_url)
            if parsed_base.scheme != "https" or not parsed_base.hostname:
                raise ValueError("MCP_PUBLIC_BASE_URL must be an HTTPS URL with a hostname.")
            parsed_issuer = urlsplit(self.mcp_jwt_issuer or "")
            if parsed_issuer.scheme not in {"https", "http"} or not parsed_issuer.hostname:
                raise ValueError(
                    "MCP_JWT_ISSUER must be an HTTP(S) URL with a hostname when "
                    "MCP_JWT_PROVIDE_DISCOVERY is enabled."
                )
        if (
            self.mcp_transport == "http"
            and self.mcp_auth_mode is AuthMode.LOCAL
            and self.mcp_host not in {"127.0.0.1", "::1", "localhost"}
        ):
            raise ValueError("HTTP on a non-loopback host requires MCP_AUTH_MODE=jwt.")
        if bool(self.tos_access_key) != bool(self.tos_secret_key):
            raise ValueError("TOS_ACCESS_KEY and TOS_SECRET_KEY must both be set or both be empty.")
        if self.tos_access_key and not self.tos_endpoint:
            raise ValueError("TOS_ENDPOINT is required when TOS credentials are set.")
        if bool(self.s3_access_key) != bool(self.s3_secret_key):
            raise ValueError("S3_ACCESS_KEY and S3_SECRET_KEY must both be set or both be empty.")
        if self.s3_access_key and not self.s3_bucket:
            raise ValueError("S3_BUCKET is required when S3 credentials are set.")
        if self.object_storage_backend == "s3" and not self.has_s3:
            raise ValueError(
                "OBJECT_STORAGE_BACKEND=s3 requires S3_ACCESS_KEY, S3_SECRET_KEY, and S3_BUCKET."
            )
        if self.object_storage_backend == "tos" and self.has_s3 and not self.has_tos:
            raise ValueError(
                "OBJECT_STORAGE_BACKEND=tos but TOS credentials are missing while S3 "
                "credentials are set. Set OBJECT_STORAGE_BACKEND=s3 or provide TOS_*."
            )
        if self.artifact_backend == "object_storage" and not self.has_object_storage:
            raise ValueError("ARTIFACT_BACKEND=object_storage requires TOS_* or S3_* credentials.")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()


def refresh_settings() -> Settings:
    """Force-reload settings from environment (e.g. after config change)."""
    get_settings.cache_clear()
    return get_settings()


def validate() -> None:
    """Validate required environment variables at startup.

    Called by ``make check-env``.  Raises if essential configuration is
    missing or invalid.  Provider credentials are optional (the server
    degrades gracefully by not registering tools for absent credentials),
    but base URLs and model bindings must be syntactically valid.
    """
    settings = get_settings()
    if not settings.modelark_base_url.startswith("https://"):
        raise ValueError("BYTEPLUS_MODELARK_BASE_URL must use HTTPS")
    if not settings.seed_audio_base_url.startswith("https://"):
        raise ValueError("BYTEPLUS_SEED_AUDIO_BASE_URL must use HTTPS")
    if not settings.vod_mediakit_base_url.startswith("https://"):
        raise ValueError("BYTEPLUS_VOD_MEDIAKIT_BASE_URL must use HTTPS")
    if settings.artifact_ttl_seconds <= 0:
        raise ValueError("ARTIFACT_TTL_SECONDS must be positive")
    if settings.mcp_inline_media_max_bytes <= 0:
        raise ValueError("MCP_INLINE_MEDIA_MAX_BYTES must be positive")
