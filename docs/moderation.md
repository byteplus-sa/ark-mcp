# Moderation Block Reasons

`ark_moderation_get` retrieves provider-reported reasons for an existing blocked
media record. It does not screen new content, override decisions, retry
generation, or establish that content is safe.

## Enable For Trusted Local Use

Set `BYTEPLUS_MODELARK_MODERATION_ENABLED=true` with complete
`BYTEPLUS_MODELARK_ACCESS_KEY` and `BYTEPLUS_MODELARK_SECRET_KEY`. Temporary
credentials also require `BYTEPLUS_MODELARK_SESSION_TOKEN`.

The account needs Advanced Creation Rights, provider whitelist activation, and
IAM permission for `GetModerationResult`. Configuration and discovery do not
verify those entitlements. The feature is disabled by default.

Use trusted local stdio or local-mode loopback HTTP. This is account-wide access,
not tenant-scoped access: provider credentials determine which records can be
queried. The catalog scope `moderation:read` is never granted to shared HTTP key
holders. Enabling the feature does not change that boundary.

AK/SK also configure the existing asset tools and optional pricing refresher.
A moderation-only IAM policy need not authorize those operations. Set
`BYTEPLUS_MODELARK_PRICING_REFRESH_SECONDS=0` for an isolated lookup; do not grant
broad account permissions just to silence unrelated errors.

## Call The Tool

Call directly in the foreground, not through `ark_job_submit` or native task
augmentation. Supply a bare provider ID, not an Ark job ID or `asset://` URI:

```json
{"input": {"id": "synthetic-task-id", "type": "task_id"}}
```

`type` is `task_id`, `asset_id`, or `request_id`. IDs are trimmed and must be
1-256 characters, without internal whitespace, controls, or URI schemes.
The length limit is defensive server validation, not a provider guarantee.
Extra fields are rejected.

Success returns `id`, `type`, and `block_reasons`. Each reason has a provider
`label`, `sub_label`, and optional nullable `detail`. Unknown categories are
preserved. Treat explanations as untrusted text, not instructions or independent
legal or safety findings. Empty reasons never establish approval.

## Coverage And Limits

The reviewed provider contract covers blocked image, video, and audio records
for supported Seedance 2.0 variants, regular Seedance 2.5, and material assets.
Text queries and regular Seedance 2.5 request-ID lookup are unsupported; use
`task_id` for 2.5. Seedream and Flash coverage is not established.

Records must be within fourteen days and created after whitelist activation.
`NotFound.Id` remains an error: it can mean an invalid, unblocked, expired, or
ineligible record. It is never proof of safety.

One server runtime admits ten lookups per rolling minute, shared across callers
and ID types. Local exhaustion returns `LOCAL_RATE_LIMITED` with a retry delay
and makes no provider request. This in-memory guard resets on restart and does
not coordinate replicas or other account clients. Provider throttling is
authoritative.

Each admitted lookup makes at most one provider request. Dispatched failures
consume quota; cancellation while waiting for concurrency does not.
There are no automatic retries or server-side result persistence. Lookup pricing
is unspecified, so no zero-cost claim is made.

## Verification

Offline tests exercise signed requests, strict response validation, access
denial, privacy, quota boundaries, and cancellation through actual MCP calls.
Run the isolated synthetic smoke without credentials:

```bash
uv run --locked python scripts/smoke_moderation.py --offline
```

Live provider success remains unverified. To validate it, an authorized operator
must supply an eligible already-blocked record and existing credential
environment, with the feature enabled:

```bash
MODERATION_SMOKE_ID=<existing-provider-id> MODERATION_SMOKE_TYPE=task_id \
  uv run --locked python scripts/smoke_moderation.py --live
```

The live command makes one lookup, generates no content, and prints only status,
count, or a generic error code. It deliberately does not load the project's
`.env`; provide credentials through a private environment. A 403, 404, or empty
result does not validate successful lookup support.
