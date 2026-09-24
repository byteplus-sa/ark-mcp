---
title: ModelArk Asset Library — Phase 0 Validation Runbook
type: plan
status: in-progress
created: 2026-09-24
updated: 2026-09-24
tags: [byteplus, modelark, assets, seedance, virtual-portrait, real-human, tsp, bytedcli, sts, validation]
source:
  - https://docs.byteplus.com/en/docs/ModelArk/2333565   # Private virtual portrait library (accessed 2026-09-23)
  - https://docs.byteplus.com/en/docs/ModelArk/2333589   # Private real-human asset library guide (accessed 2026-09-23)
  - https://docs.byteplus.com/en/docs/ModelArk/2318271   # CreateAsset API reference (accessed 2026-09-23)
  - https://docs.byteplus.com/en/docs/ModelArk/2377608   # Advanced Creation Rights purchase guide (accessed 2026-09-23)
  - https://bytedance.sg.larkoffice.com/docx/EgGGdsGbooNIAbx8d61lQ7gvgSc   # BytePlus through TSP: SA quick start (internal, accessed 2026-09-24)
  - https://bytedance.sg.larkoffice.com/docx/TvIbdjCuHoHpytxDIH3l4TLJgdc   # TSP bytedcli guide (internal, accessed 2026-09-24)
related:
  - "[[PLAN_MODELARK_ASSET_LIBRARY]]"
  - "[[SPEC_VOD_OPENAPI_PROVIDER_CONTRACT]]"
---
<!-- markdownlint-disable MD013 MD025 -->

# ModelArk Asset Library — Phase 0 Validation Runbook

This is the hands-on companion to
[`PLAN_MODELARK_ASSET_LIBRARY.md`](PLAN_MODELARK_ASSET_LIBRARY.md). The
feature plan says **what** to build. This runbook shows how to get working
credentials and prove the provider API works end to end **before** any server
code is written.

## 1. What we are trying to do

**Goal:** let ark-mcp users generate Seedance video (and, where the provider
allows it, Seedream images and Seed Audio) with **real people's faces**,
**private virtual characters** and **copyrighted IP**. This uses the ModelArk
**private asset library** that comes with **Dreamina Seedance Advanced
Creation Rights**.

How the provider models it:

| Concept | Meaning |
|---|---|
| **Asset group** | One subject: one real person, one virtual character, one product. All files for the same subject go in the **same group**. |
| **Asset** | One trusted image, video or audio file inside a group. It gets an ID like `asset-20260924…-xxxxx`. |
| **Group type `AIGC`** | *Virtual Portrait* tab in the console. Created by API (`CreateAssetGroup`). Must **not** resemble any real person. |
| **Group type `LivenessFace`** | *Real-human* tab. Created only through a real-person liveness verification (`CreateVisualValidateSession` → H5 link → `GetVisualValidateResult`). Every upload is face-matched to the verified person. |
| **Copyright IP** | *Copyright IP* tab. Console only; no API. Asset IDs copied from the console can be used in Seedance. |
| **Using an asset** | Put `asset://<asset_id>` in the Seedance `content[].image_url/video_url/audio_url.url`. In the prompt, refer to it as "Image 1 / Video 1", never by ID. |

What ark-mcp will add (full detail in the feature plan):

1. `ark_asset_*` MCP tools: create or ensure a group, upload assets, get, list,
   update, (gated) delete, and real-person verification start/result.
2. `asset://` references accepted natively by the Seedance 2.0 and 2.5
   tools. Seedream and Seed Audio rejected native references in the Phase 0
   probe, so they need an opt-in, experimental "resolve to temporary URL"
   mode.
3. The "same subject → same asset group" rule, enforced by the tool shapes.

### Why a second set of credentials is needed

| Credential | Where it works | Used for |
|---|---|---|
| ModelArk **API key** (Bearer) | `ark.ap-southeast.bytepluses.com/api/v3` (data plane) | Seedance/Seedream generation. Only Seedance accepted `asset://` in the live probe. Already configured. |
| BytePlus **AK/SK** (+ session token for temporary keys) | `ark.ap-southeast-1.byteplusapi.com` (OpenAPI, HMAC-SHA256 signed) | **Managing** the asset library: `CreateAssetGroup`, `CreateAsset`, `GetAsset`, etc. |

The asset docs state that the Assets API requires Access Key authentication.
An API key cannot create groups or upload assets.

## 2. What is already verified

| Check | Result |
|---|---|
| OpenAPI host, action name, version (`ark`, `2024-01-01`), signing format | ✅ The endpoint answers signed requests and returns a standard `ResponseMetadata.Error` envelope. |
| Error envelope shape | ✅ `{"ResponseMetadata":{…,"Error":{"CodeN":100009,"Code":"InvalidAccessKey","Message":"…"}}}` |
| Session-token header (`X-Security-Token`, signed) | ✅ The server parses it. A dummy token returns `InvalidSecretToken` (100026), not `InvalidAccessKey`. |
| ByteCloud / agent AK/SK (`AKEE…` / `SKPK…`) used directly | ❌ `InvalidAccessKey`. These authenticate you to **ByteCloud/TSP**, not BytePlus. Use them (or `bytedcli` login) to *request* BytePlus STS credentials instead. |
| Test image | ✅ A synthetic cartoon character, generated with Seedream and uploaded to object storage (presigned URL). |
| TSP-issued BytePlus STS credentials | ✅ `bytedcli` device login and `tsp sts user-token get` succeeded for a BytePlus IAM user. Credentials were used only in process memory. |
| `ListAssetGroups` with real credentials | ✅ Succeeded in the `default` project with `Filter.GroupType=AIGC`. The API returned `MissingParameter.Filter` when `Filter` was omitted. |
| `CreateAssetGroup` / `CreateAsset` / `GetAsset` with real credentials | ✅ An AIGC test group and synthetic image asset were created; `GetAsset` reported `Active` on the first poll. |
| Seedance 2.5 with `asset://` reference | ✅ A raw data-plane request using the existing API key completed successfully. The 4.04-second H.264 video is 480 × 854 at 24 fps and visually retains the cartoon character. |
| Seedream 5.0 Pro with `asset://` image input | ❌ HTTP 400 `InvalidParameter`: `image` rejected as an invalid URL. Native `asset://` input is unsupported in this probe. |
| Seed Audio 1.0 with `asset://` image reference | ❌ HTTP 400, code `45001132`: its downloader rejected the unsupported `asset` URL scheme. Native `asset://` input is unsupported in this probe. |

The live probe in the `default` project created group
`group-20260924075329-67prc` and asset
`asset-20260924075358-s9g98`. The reusable reference is
`asset://asset-20260924075358-s9g98`. The synthetic source image is stored
under object key
`asset-library-probe/image/bb010f72-d317-411d-9353-66876140d9d4`.
No presigned URL or credential is recorded here.

The Seedance 2.5 probe used task `cgt-20260924075701-fncv2`, model
`dreamina-seedance-2-5-260628`, the asset URI above as a
`reference_image`, 480p, 9:16, four seconds, no generated audio, and no
watermark. The exact prompt was: “Image 1 is an original fictional 3D
cartoon character. Keep the character design consistent. The character
blinks once and gives a small friendly wave, with subtle head movement
against a clean neutral background.” The local review copy is
`.artifacts/asset-library-probe/seedance-asset-reference.mp4` (gitignored).

## 3. Getting BytePlus credentials through TSP (bytedcli)

TSP issues **temporary** BytePlus credentials (access key starting with
`AKTP`, secret key, **session token**) for a specific BytePlus account and IAM
user. No long-lived BytePlus keys are needed.

### 3.1 Prerequisites

- **Corporate VPN / internal network is on.** Without it, `bnpm.byted.org`
  resolves to the local gateway and npm fails with
  `ERR_TLS_CERT_ALTNAME_INVALID` (cert for `ezxcess.antlabs.com`). Do **not**
  disable TLS checks; connect to the VPN instead.
- Node.js/npm and Python 3.
- The **BytePlus account ID** and **IAM username** to act as. The IAM user
  needs **`ArkFullAccess`** on the ModelArk project (`default` unless changed).
- Your ByteCloud identity must be authorized in TSP to request that IAM user's
  credentials.
- The BytePlus account has **Advanced Creation Rights** active. The free
  *Entry* tier is enough for this test: API access, 50 assets / 50 groups,
  `CreateAsset` 3 QPM.

### 3.2 Install bytedcli

```bash
NPM_CONFIG_REGISTRY=https://bnpm.byted.org npm install -g @bytedance-dev/bytedcli@latest
```

```bash
bytedcli --version
```

Needs version 0.131.0 or later; 0.160.0 was verified on 2026-09-24. If npm
isn't available, the internal guide provides a standalone binary installer.

### 3.3 Log in (device flow)

Interactive, with a browser on the same machine:

```bash
bytedcli --site i18n-bd auth login
```

For an agent or a headless session, use the two-step form. The human opens
the returned `verification_uri_complete` and approves it; the link expires
after about 15 minutes.

```bash
bytedcli --site i18n-bd --json auth login --begin
```

```bash
bytedcli --site i18n-bd --json auth login --complete
```

Check the result:

```bash
bytedcli --site i18n-bd --json auth status
```

Expect `"authenticated": true`.

### 3.4 Request BytePlus STS credentials

According to the TSP bytedcli guide (`tsp sts` module):

```bash
bytedcli --site i18n-bd --json tsp sts user-token get --account-id <byteplus-account-id> --user-name <iam-username>
```

- `--account-id` is required: the BytePlus account ID.
- `--user-name` is optional and defaults to your email prefix.
- The installed `bytedcli` 0.160.0 accepts the global `--site` flag. Its
  `tsp sts user-token get` command does not accept `--tsp-site` or
  `--identity-type`; the account ID routes the STS request to the matching
  TSP site.
- `--json` gives structured output under `data.Result` with `AccessKey`,
  `SecretKey`, and `SessionToken`.

The output contains the access key (`AKTP…`), secret key and session token.
Treat them as secrets: don't paste them into chats, tickets or commits, and
don't assume a fixed lifetime. Request a fresh set when you get
`InvalidAccessKey` or an expiry error.

Export them **in the same terminal** that will run the probe:

```bash
export BYTEPLUS_MODELARK_ACCESS_KEY='AKTP...' BYTEPLUS_MODELARK_SECRET_KEY='...' BYTEPLUS_MODELARK_SESSION_TOKEN='...'
```

Exported variables exist only in that one shell. New terminal tabs and the
Claude Code shell do not inherit them.

### 3.5 Letting Claude Code run it

Claude Code's auto-mode safety check blocks `bytedcli tsp …` commands as
credential access. To let the agent fetch credentials itself, add these to the
Claude Code permission allow list:

```text
Bash(bytedcli --site i18n-bd:*)
Bash(bytedcli --site i18n-bd --json tsp sts:*)
```

Otherwise, run section 3.4 and the probe yourself.

## 4. Run the probe

Scripts (no secrets):

- [`scripts/ark_openapi_sign.py`](../scripts/ark_openapi_sign.py): signs one
  ModelArk OpenAPI call (BytePlus v4 HMAC-SHA256, service `ark`) and sends it
  with curl. It sends `X-Security-Token` when
  `BYTEPLUS_MODELARK_SESSION_TOKEN` is set. It never prints credentials.
- [`scripts/asset_library_probe.sh`](../scripts/asset_library_probe.sh): runs
  `ListAssetGroups` (read-only credential check) → `CreateAssetGroup` (AIGC) →
  `CreateAsset` → polls `GetAsset` until `Active`/`Failed`. It **never
  deletes** anything.

```bash
scripts/asset_library_probe.sh '<public-https-url-of-a-synthetic-character-image>' mcp-api-test
```

To get a fresh public URL for a local image, use the ark-mcp `media_upload`
tool (it returns a presigned HTTPS URL; use `expires_in_seconds: 86400`), or
`media_presign` on an existing object key.

Single calls, for debugging:

```bash
python3 scripts/ark_openapi_sign.py ListAssetGroups '{"Filter":{"GroupType":"AIGC"},"MaxResults":5,"ProjectName":"default"}'
```

```bash
python3 scripts/ark_openapi_sign.py GetAsset '{"Id":"asset-...","ProjectName":"default"}'
```

**Test image rule:** Virtual Portrait (`AIGC`) assets must not resemble a real
person; you sign a commitment letter covering this. Use a clearly
synthetic/cartoon character. Real faces go through the Real-human
(`LivenessFace`) verification flow, which the person themselves must
complete.

### Expected outcome

```text
RESULT group=group-2026…-xxxxx asset=asset-2026…-xxxxx status=Active uri=asset://asset-2026…-xxxxx
```

### Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `InvalidAccessKey` (100009) | Wrong key type (ByteCloud `AKEE…` instead of BytePlus `AKTP…`/`AKLT…`) or expired STS key | Request fresh STS credentials (3.4) |
| `InvalidSecretToken` (100026) | Session token missing, truncated, or from a different credential set | Export all three values from the same `user-token get` call |
| `AccessDenied` / 403 | IAM user lacks `ArkFullAccess` on the project | Grant the policy in BytePlus IAM |
| Group created but asset not found | `ProjectName` mismatch between calls | Use the same project everywhere (`BYTEPLUS_MODELARK_PROJECT_NAME`) |
| `CreateAsset` throttled | Entry tier allows 3 QPM | Wait, or upgrade the tier |
| First `CreateAssetGroup` rejected | Authorization letter not yet signed | Sign it once in the console (Model activation → Advanced Creation Rights / Manage assets) |
| Status `Failed` | Preprocessing or moderation rejected the file | Check format and limits (image 300–6000 px, W/H 0.4–2.5, < 30 MB) and content |

## 5. After the probe succeeds

1. **Seedance `asset://` test (API key only): complete.** The raw
   data-plane request succeeded. The MCP Seedance tools now accept
   `asset://<asset_id>` (branch `feat/modelark-asset-library`); a live run
   through the MCP tools is still pending.
2. **Seedream and Seed Audio probe: complete.** Both rejected native
   `asset://` input. The feature plan's optional resolve-to-URL mode is
   required for these providers and remains experimental pending written
   rights confirmation for real-human and copyright assets.
3. **Record error codes** for quota exhaustion, face mismatch
   (`LivenessFace`) and project mismatch.
4. **Contract spec: complete for the verified AIGC path.** The confirmed
   behavior is in
   [`SPEC_MODELARK_ASSET_LIBRARY_CONTRACT.md`](../specs/SPEC_MODELARK_ASSET_LIBRARY_CONTRACT.md).
   Phase 1 can use that contract while the unverified paths remain labeled.
5. Clean up: the test group `mcp-api-test` remains for review and MCP tool
   integration tests. Delete it in the console when no longer needed; assets
   count against the tier quota.

## 6. Open items

- [x] BytePlus account ID and IAM username for the test (with asset API access).
- [x] TSP authorization for that identity confirmed.
- [x] AIGC management API probe run to `Active`.
- [x] Seedance `asset://` generation confirmed.
- [x] Seedream / Seed Audio `asset://` behavior recorded.
- [ ] Remove the test group after tool integration testing and review.
- [ ] Written BytePlus confirmation on resolve mode for real-human and
      copyright assets (feature plan, open question 2).
- [ ] Rotate the ByteCloud agent key pair that was pasted into a chat session
      on 2026-09-24.
