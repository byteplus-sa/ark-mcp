# API Keys Guide

For the separate key members use to connect to a hosted MCP server, see
[shared MCP key generation](self-hosting.md#shared-mcp-key).
That key is not a BytePlus provider key. The credentials below stay on the
server and must not be distributed to shared-server members.

For administrator-managed BytePlus deployments, follow
[Secret Manager setup and provider-key updates](secret-manager.md).
That workflow pins the complete bundle of supported provider keys. An
approved sync updates the VM and preserves the separate shared MCP connection key.
It does not manage IAM or object-storage credentials.

This server calls several BytePlus services. Each uses a distinct credential
and auth scheme — they are **not interchangeable**. You only need the keys for
the products you want to use; absent credentials simply skip registering that
tool set.

| Service | Env var | Auth header | Tools it enables |
|---|---|---|---|
| ModelArk | `BYTEPLUS_MODELARK_API_KEY` | `Authorization: Bearer <key>` | Seedream, Seedance, image/video/audio understanding |
| Seed Speech | `BYTEPLUS_SEED_SPEECH_API_KEY` | `X-Api-Key: <key>` | Seed Audio (full-scene audio), Seed TTS 3.0 (text-to-speech), Speech-to-Text (ASR) |
| Vision AI (OmniHuman) | `BYTEPLUS_CV_API_KEY` | `Authorization: Bearer <key>` | `omnihuman_*` tools (also needs `BYTEPLUS_OMNIHUMAN_ENABLED=true`) |
| ModelArk OpenAPI | `BYTEPLUS_MODELARK_ACCESS_KEY` + `BYTEPLUS_MODELARK_SECRET_KEY` (+ session token for STS) | AK/SK V4 signing | Asset tools, cached pricing, and opt-in `ark_moderation_get`; account-wide administration is local-only |
| TOS | `TOS_ACCESS_KEY` + `TOS_SECRET_KEY` + `TOS_BUCKET` | AK/SK signing | `media_upload`, `media_presign` |
| S3 | `S3_ACCESS_KEY` + `S3_SECRET_KEY` + `S3_BUCKET` | AK/SK signing | `media_upload`, `media_presign` |

With [guided setup](setup/install.md), your agent prepares a private blank
credential file and opens it for you. **Enter keys there, not in chat or MCP
client JSON.** You do not need to copy the full example configuration or
configure services you do not use. If using a shared server, its operator
manages provider keys; your client uses the operator's connection credentials.

All keys below are **startup configuration**, never tool arguments. The
examples are field-name references for that private file, not shell commands.
Preserve existing files and keep credentials outside repositories and shared
folders. See [manual credential setup](setup/manual-install.md#prepare-private-credentials)
only when preparing the file yourself.

## ModelArk

ModelArk provides **Seedream**, **Seedance**, and multimodal understanding.
The key is sent as a Bearer token.

**Env vars:**

| Variable | Default | Purpose |
|---|---|---|
| `BYTEPLUS_MODELARK_API_KEY` | empty | ModelArk API key |
| `BYTEPLUS_MODELARK_BASE_URL` | `https://ark.ap-southeast.bytepluses.com/api/v3` | Region-scoped data-plane host |

**How to get the key:**

1. Sign in to the BytePlus console at **<https://console.byteplus.com>**.
2. Open **ModelArk** (Ark) and navigate to **API Key** management.
3. Create a new API key. Copy it immediately — it is shown once.
4. (Optional) Create an **inference endpoint** for the Seedream or Seedance
   model you want to use, then set its ID as `SEEDREAM_DEFAULT_MODEL` or
   `SEEDANCE_DEFAULT_MODEL`. The built-in defaults already have known families;
   a custom endpoint ID must be bound via `SEEDREAM_MODEL_BINDINGS` /
   `SEEDANCE_MODEL_BINDINGS`.

```dotenv
BYTEPLUS_MODELARK_API_KEY=your-modelark-key-here
```

The base URL is region-scoped. If your account is in a different region, update
`BYTEPLUS_MODELARK_BASE_URL` to match.

## ModelArk OpenAPI (assets and moderation)

Signed OpenAPI calls use BytePlus IAM AK/SK, not the ModelArk Bearer key.
Configure `BYTEPLUS_MODELARK_ACCESS_KEY` and `BYTEPLUS_MODELARK_SECRET_KEY`
together; temporary credentials also need `BYTEPLUS_MODELARK_SESSION_TOKEN`.
See [asset configuration](configuration.md#private-asset-library-aksk) for the
OpenAPI host and signing region.

Moderation additionally needs `BYTEPLUS_MODELARK_MODERATION_ENABLED=true`
(default off), Advanced Creation Rights, whitelist activation and IAM
`GetModerationResult`. It is account-wide, foreground-only trusted stdio or
local-mode loopback access; `moderation:read` is never granted to shared HTTP.
AK/SK also configure asset tools and cached pricing. For a moderation-only IAM
policy, disable pricing refresh with `BYTEPLUS_MODELARK_PRICING_REFRESH_SECONDS=0`
and do not grant unrelated permissions. Discovery verifies configuration only;
live lookup success is unverified. See [moderation.md](moderation.md).

## Seed Speech

Seed Speech serves **Seed Audio** (full-scene audio generation),
**Seed TTS 3.0** (text-to-speech) and **speech-to-text (ASR)**. A **single
Seed Speech API key** is reused by all three tools. It is separate from
ModelArk and uses a different auth header (`X-Api-Key`, not Bearer).
Seed TTS and Seed Audio share `BYTEPLUS_SEED_AUDIO_BASE_URL`; there is no
new TTS environment variable. See [Seed TTS 3.0](seed-tts.md).

**Env vars:**

| Variable | Default | Purpose |
|---|---|---|
| `BYTEPLUS_SEED_SPEECH_API_KEY` | empty | Seed Speech API key (Seed Audio + Seed TTS + ASR) |
| `BYTEPLUS_SEED_AUDIO_BASE_URL` | `https://voice.ap-southeast-1.bytepluses.com` | Seed Audio and Seed TTS host |
| `SEED_SPEECH_ASR_BASE_URL` | `https://voice.ap-southeast-1.bytepluses.com` | Seed Speech ASR host |
| `SEED_SPEECH_ASR_POLL_INTERVAL_SECONDS` | `3.0` | Seconds between ASR query polls |
| `SEED_SPEECH_ASR_POLL_MAX_SECONDS` | `600.0` | Maximum total seconds to wait for ASR result |

**How to get the key:**

1. Sign in to **<https://console.byteplus.com>**.
2. Open **BytePlus Voice** (or Seed Speech / Speech Studio).
3. Navigate to API key or application management and create a key.
4. Copy the key. It is distinct from your ModelArk key.

The key must belong to an application with access to the capability you use:
Seed Audio for `seed_audio_generate`, TTS 3.0 and an authorized voice for
`seed_tts_generate`, or the ASR resource for `speech_to_text`. If a
capability is not enabled on the application, provider calls for it are
rejected with an auth/resource error even though the key itself is valid.

```dotenv
BYTEPLUS_SEED_SPEECH_API_KEY=your-seed-speech-key-here
```

## Seed Speech ASR (Speech-to-Text)

Speech-to-text (ASR) **reuses `BYTEPLUS_SEED_SPEECH_API_KEY`** — the same key
that powers Seed Audio and Seed TTS. When the key is set, the `speech_to_text` tool is
registered automatically; it submits audio via HTTP, polls until
transcription is complete, and returns the full `TranscriptionResult` through
a required background job, submitted through the ordinary `ark_job_*` tools or
native MCP task augmentation on a task-capable client.

The ASR-specific settings below only control the host and polling behavior,
not the credential.

**Env vars:**

| Variable | Default | Purpose |
|---|---|---|
| `SEED_SPEECH_ASR_BASE_URL` | `https://voice.ap-southeast-1.bytepluses.com` | Seed Speech ASR HTTP host |
| `SEED_SPEECH_ASR_POLL_INTERVAL_SECONDS` | `3.0` | Seconds between ASR query polls |
| `SEED_SPEECH_ASR_POLL_MAX_SECONDS` | `600.0` | Maximum total seconds to wait for ASR result |

## BytePlus Vision AI (OmniHuman)

**Env vars:**

| Variable | Default | Purpose |
|---|---|---|
| `BYTEPLUS_CV_API_KEY` | empty | Vision AI Bearer key |
| `BYTEPLUS_OMNIHUMAN_ENABLED` | `false` | Registers the `omnihuman_*` tools |
| `BYTEPLUS_CV_BASE_URL` | `https://openapi.cv.bytepluses.com` | Vision AI base URL |

Create the key under Access Key Management in the BytePlus console and make
sure the account has OmniHuman 2.0 enabled. The same service also accepts
AK/SK-signed requests on `cv.byteplusapi.com`; this server uses the API-key
path only.

## Object storage (TOS or S3)

Object storage is **optional** but enables several workflows: the `media_upload` and
`media_presign` tools and Seedance video references
(URL-only). It uses Access Key / Secret Key (AK/SK) signing, not a bearer
token. Select the backend with `OBJECT_STORAGE_BACKEND` (`tos` default, or
`s3`).

### TOS backend

**Env vars:**

| Variable | Default | Purpose |
|---|---|---|
| `TOS_ACCESS_KEY` | empty | Access Key (AK) |
| `TOS_SECRET_KEY` | empty | Secret Key (SK) |
| `TOS_SECURITY_TOKEN` | empty | Optional temporary token (STS only) |
| `TOS_BUCKET` | empty | Target bucket name |
| `TOS_REGION` | `ap-southeast-1` | Bucket region |
| `TOS_ENDPOINT` | `tos-ap-southeast-1.bytepluses.com` | TOS API endpoint |
| `TOS_PRESIGN_TTL_SECONDS` | `1800` | Presigned URL validity in seconds (60–604800) |

`TOS_ACCESS_KEY` and `TOS_SECRET_KEY` must **both** be set or **both** be
empty. All three of AK, SK, and bucket must be set to register the
`media_upload` and `media_presign` tools.

**How to get the keys:**

1. Sign in to **<https://console.byteplus.com>**.
2. Open **TOS** (Object Storage).
3. Create a **bucket** (keep it **private**) in your target region. Note the
   bucket name and region — set them as `TOS_BUCKET` and `TOS_REGION`.
4. For the access keys: open **IAM** (Identity and Access Management) and
   create an access key pair (AK + SK) for a user or service account that has
   read/write permission on that bucket. Copy both immediately — the SK is
   shown once.

```dotenv
TOS_ACCESS_KEY=your-access-key
TOS_SECRET_KEY=your-secret-key
TOS_BUCKET=your-private-bucket
TOS_REGION=ap-southeast-1
TOS_ENDPOINT=tos-ap-southeast-1.bytepluses.com
```

**Security notes:**

- Keep the bucket **private**. The server never makes objects public; it
  generates short-lived presigned GET URLs (default 30 minutes, configurable via
  `TOS_PRESIGN_TTL_SECONDS`) for individual objects.
- Uploaded objects are **not auto-deleted** by this server. Configure a TOS
  bucket lifecycle rule to expire objects under the upload prefixes
  (`stt-input/`, `references/`) to control storage cost over time.
- For temporary credentials (STS), also set `TOS_SECURITY_TOKEN`.

### S3 backend

| Variable | Default | Purpose |
|---|---|---|
| `S3_ACCESS_KEY` | empty | S3 access key ID |
| `S3_SECRET_KEY` | empty | S3 secret access key |
| `S3_BUCKET` | empty | Target bucket name |
| `S3_REGION` | `us-east-1` | AWS region |
| `S3_ENDPOINT` | empty | Custom endpoint for S3-compatible storage |
| `S3_PRESIGN_TTL_SECONDS` | `1800` | Presigned URL validity in seconds (60–604800) |
| `OBJECT_STORAGE_BACKEND` | `tos` | Select active backend: `tos` or `s3` |

`S3_ACCESS_KEY` and `S3_SECRET_KEY` must **both** be set or **both** be
empty. All three of AK, SK, and bucket must be set to register the
`media_upload` and `media_presign` tools with the S3
backend.

When `S3_ENDPOINT` is set, path-style addressing is used automatically for
S3-compatible storage (MinIO, R2, TOS-via-boto3).

## Verifying your setup

After saving the private file, reconnect Ark through your client's MCP
settings, taking care to finish active jobs first. The client starts the
server automatically. Use the [discovery-only connection check](integrations/index.md#verify-without-paid-calls);
do not start development mode just to test keys.

The `seed-health://status` MCP resource reports `ModelArk configured`,
`Seed Audio configured`, `STT configured`, `TOS configured`, `S3 configured`,
and `Object storage backend` values per
provider. The separate `/health` HTTP route is a liveness probe that returns
only `{"status": "healthy"}`. A disabled service may have a missing key or
feature flag. Ask your setup agent to check names and the configured
`ARK_MCP_ENV_FILE` path without reading or printing key values.

If a tool is missing entirely, its provider credentials were absent at startup.
Tools are registered conditionally based on which keys are present.
Discovery confirms configuration, not provider-key validity or account access.

## Quick reference

```dotenv
# ModelArk — image + video
BYTEPLUS_MODELARK_API_KEY=
BYTEPLUS_MODELARK_BASE_URL=https://ark.ap-southeast.bytepluses.com/api/v3

# Seed Speech — Seed Audio + Seed TTS 3.0 + ASR (STT), one key
BYTEPLUS_SEED_SPEECH_API_KEY=
BYTEPLUS_SEED_AUDIO_BASE_URL=https://voice.ap-southeast-1.bytepluses.com

# Seed Speech ASR — host + polling only (reuses the key above)
SEED_SPEECH_ASR_BASE_URL=https://voice.ap-southeast-1.bytepluses.com
SEED_SPEECH_ASR_POLL_INTERVAL_SECONDS=3.0
SEED_SPEECH_ASR_POLL_MAX_SECONDS=600.0

# Object storage — TOS (optional, default backend)
TOS_ACCESS_KEY=
TOS_SECRET_KEY=
TOS_BUCKET=
TOS_REGION=ap-southeast-1
TOS_ENDPOINT=tos-ap-southeast-1.bytepluses.com
TOS_PRESIGN_TTL_SECONDS=1800

# Object storage — S3 (optional alternative backend)
S3_ACCESS_KEY=
S3_SECRET_KEY=
S3_BUCKET=
S3_REGION=us-east-1
S3_ENDPOINT=
S3_PRESIGN_TTL_SECONDS=1800
OBJECT_STORAGE_BACKEND=tos
```

Console: **<https://console.byteplus.com>**
Docs: **<https://docs.byteplus.com>**
