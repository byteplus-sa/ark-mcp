# Private Asset Library (Real People, Virtual Portraits, Copyright IP)

The ModelArk **private asset library** stores trusted media that Seedance 2.0
and 2.5 may use as references. It is how you legitimately generate video with
**verified real people**, **your own virtual characters**, and
**copyrighted IP** made available in the console. The library comes with
**Dreamina Seedance Advanced Creation Rights**. The free *Entry* tier already
allows API access, with 50 assets / 50 groups and 3 `CreateAsset` calls per
minute.

Design and rationale: [`plans/PLAN_MODELARK_ASSET_LIBRARY.md`](../plans/PLAN_MODELARK_ASSET_LIBRARY.md).
Validation runbook: [`plans/PLAN_MODELARK_ASSET_LIBRARY_VALIDATION.md`](../plans/PLAN_MODELARK_ASSET_LIBRARY_VALIDATION.md).

## Concepts

| Concept | Meaning |
|---|---|
| **Asset group** | One subject: one real person, one virtual character, one product. Keep all of a subject's files in the same group. |
| **Asset** | One image, video or audio file in a group, e.g. `asset-20260318035710-kctzf`. |
| **`AIGC` group** | *Virtual Portrait* tab. Created by API. Must **not** resemble any real person. |
| **`LivenessFace` group** | *Real-human* tab. Created only by a real-person liveness verification that the person completes themselves. Every upload is face-matched to that person. |
| **Copyright IP** | *Copyright IP* tab. Console only; copy asset IDs from the console. |
| **`asset://<asset_id>`** | How an asset is referenced in generation requests. |

## Setup

Asset **management** needs BytePlus IAM AK/SK, which is separate from the
ModelArk API key. The tools are registered only when both keys are set:

```bash
BYTEPLUS_MODELARK_ACCESS_KEY=AKLT...        # or AKTP... for STS temporary keys
BYTEPLUS_MODELARK_SECRET_KEY=...
BYTEPLUS_MODELARK_SESSION_TOKEN=            # required only for AKTP... keys
```

The IAM user needs `ArkFullAccess` on the ModelArk project. All settings are
listed in [configuration.md](configuration.md#private-asset-library-akssk).

**Using** assets in Seedance needs only the normal `BYTEPLUS_MODELARK_API_KEY`:
`asset://` references work even without AK/SK, for example for assets created
or authorized in the console.

## Tools

| Tool | Provider action | Notes |
|---|---|---|
| `ark_asset_group_ensure` | `ListAssetGroups` + `CreateAssetGroup` | Reuses the single AIGC group named exactly `subject`, or creates it. Fails with `ambiguous_asset_group` if several share the name. |
| `ark_asset_group_create` | `CreateAssetGroup` | Always creates a new AIGC group. |
| `ark_asset_group_get` / `ark_asset_group_list` | `GetAssetGroup` / `ListAssetGroups` | List filters by `group_type` (`AIGC` default, or `LivenessFace`), fuzzy `name`, `group_ids`. Paged with `next_token`. |
| `ark_asset_group_update` | `UpdateAssetGroup` | Name and description. |
| `ark_asset_create` | `CreateAsset` ×N, then `GetAsset` polling | Up to 20 files of **one** subject into one group (`group_id` or `subject`). Sources are public HTTPS `url`s or `media_upload` `object_key`s. Waits until Active/Failed by default. Per-file errors are reported inline. Background-job capable. |
| `ark_asset_get` / `ark_asset_list` | `GetAsset` / `ListAssets` | Status, type, group, `asset_uri`, `last_inference_time`, and a temporary `preview_url`. |
| `ark_asset_update` | `UpdateAsset` | Rename (search only; the model never sees names). |
| `ark_asset_verification_start` | `CreateVisualValidateSession` | Returns an H5 liveness link for the person to open, plus a token valid for about 30 minutes. |
| `ark_asset_verification_result` | `GetVisualValidateResult` | Returns the verified person's `LivenessFace` `group_id`; `wait_seconds` polls. Background-job capable. |
| `ark_asset_delete` / `ark_asset_group_delete` | `DeleteAsset` / `DeleteAssetGroup` | **Irreversible.** Registered only with `BYTEPLUS_MODELARK_ASSETS_ALLOW_DELETE=true`; each call needs `confirm: true`. Deleting a nonempty AIGC group also removes its assets. |

JWT scopes: `assets:read`, `assets:write`, `assets:verify`, `assets:delete`.
Asset management acts on the whole BytePlus account, not per MCP principal, so
grant these scopes only to trusted callers.

## Workflows

### Virtual character → Seedance

1. Make or collect clearly fictional images of the character: a front-facing
   close-up and a full-body shot, portrait orientation.
2. `media_upload` each local file (or use public HTTPS URLs).
3. `ark_asset_create` with `subject: "Teal Hero"` and the sources. All files
   land in the same AIGC group.
4. When `all_active` is true, pass the returned `asset_uri` values to
   `seedance_2_5_create_task`, for example `images: ["asset://asset-…"]`, and
   refer to them in the prompt as "Image 1", "Image 2" by position, never by ID.

### Verified real person → Seedance

1. `ark_asset_verification_start` with an HTTPS `callback_url`. Send the
   `h5_link` **only** to the person being verified; they consent and complete
   the liveness check themselves.
2. `ark_asset_verification_result` with the token (use `wait_seconds` to
   poll). It returns that person's `LivenessFace` `group_id`.
3. `ark_asset_create` with that `group_id` and **only that person's** media.
   Uploads whose face does not match are rejected.
4. Use the `asset://` references in Seedance as above.

Real-human assets that another account authorized to you through the
console QR flow can be used by ID in Seedance, but the management tools can't
see them.

### Delete an asset or group

**Deletion is opt-in and permanent.** Set
`BYTEPLUS_MODELARK_ASSETS_ALLOW_DELETE=true` before starting the MCP server;
the two delete tools then register. Inspect the target with `ark_asset_get` or
`ark_asset_group_get`, and list a group's members with `ark_asset_list` before
deleting it. Pass the exact ID and `confirm: true`:

```json
{"input":{"asset_id":"asset-EXAMPLE","confirm":true}}
```

Use that input with `ark_asset_delete`. For `ark_asset_group_delete`, pass
`{"input":{"group_id":"group-EXAMPLE","confirm":true}}`.
The AIGC group deletion path was verified live with a remaining asset: both
the group and that asset returned 404 afterward. If a delete call times out or
returns 5xx, read the target again before any retry because the provider may
already have deleted it.

### Copyright IP

Choose the IP and accept its terms in the console (*Copyright Library*), copy
the asset ID, and use `asset://<id>` in Seedance. Content from CJ7 (a film
directed by Stephen Chow) is billed at 1.1× the video price.

## `asset://` in generation tools

| Tool family | Behaviour |
|---|---|
| Seedance 2.0 / 2.5 (`images`, `videos`, `audios`) | **Native.** Passed through unchanged. With AK/SK set, a `GetAsset` preflight (`SEEDANCE_ASSET_PREFLIGHT`) stops the call before billing if an asset is `Processing` or `Failed`. An asset the account can't look up does not block. |
| Seedream (`images`) and Seed Audio (`audio_references`, `image_reference`) | **Not native.** The provider returned HTTP 400 in the 2026-09-24 probe. With `BYTEPLUS_MODELARK_ASSET_REFERENCE_MODE=off` (default) the tool rejects `asset://` locally. With `resolve` (experimental, needs AK/SK) it swaps in the asset's temporary `GetAsset` URL after checking it is `Active` and the right type. |
| Everything else (understanding, 3D, VOD, speech-to-text) | Rejected. |

> **Resolve mode caveat.** In resolve mode the model receives an ordinary URL,
> without the asset library's trust or authorization guarantees, and provider
> moderation may still block real faces. Whether verified real-human or
> copyright material may be sent to Seedream or Seed Audio this way is not yet
> confirmed by BytePlus. Treat it as experimental.

## Limits

| Type | Formats | Limits |
|---|---|---|
| Image | jpeg, png, webp, bmp, tiff, gif, heic/heif | W/H 0.4–2.5; each side 300–6000 px; < 30 MB |
| Video | mp4, mov | 2–30 s; 24–60 fps; each side 300–6000 px; ≤ 200 MB |
| Audio | wav, mp3 | 2–30 s; ≤ 15 MB |

- `CreateAsset` is asynchronous with no upload-time SLA. The server spaces
  calls to `BYTEPLUS_MODELARK_ASSET_CREATE_QPM`: 3 on Entry, 120 on Advanced,
  300 on Premium.
- `ListAssetGroups` and `ListAssets` require a group-type filter; the tools
  default it to `AIGC`.
- Assets live in one `ProjectName` and only work with inference endpoints in
  the same project.
- When a paid tier lapses, you can't add assets during the 15-day grace period.
  After it, **assets created during the paid period are deleted permanently**.

## Security notes

- AK/SK are startup configuration only (env or `/run/secrets`), never tool
  arguments. Prefer a dedicated IAM user scoped to the asset project, or STS
  keys with a session token.
- The verification `h5_link` embeds temporary credentials. The server never
  logs it or the token.
- `CreateAsset` source URLs pass the same SSRF policy as other media URLs.
  `asset://` is never fetched by the server.
- `skip_moderation` is off by default and only takes effect after content
  pre-filter is disabled in the console.
