---
title: Seedream 5.5
description: Generate and edit images, create transparent PNGs, and preview Seedream 5.5 requests through Ark MCP.
---

Seedream 5.5 uses **`dola-seedream-5-5-261028`** through the existing
`seedream_generate_image` tool. Generate from text, edit reference images,
target a point or region, or create independent variations.

This guide describes the server revision built with this documentation.
Older installations, including `v0.2.0`, may not include 5.5.
Follow [Update and Roll Back](setup/updating.md) to select a reviewed revision.
Check `ark_list_models` for the models configured on your server; discovery
does not verify provider account access.

## Create a Transparent Image

After [connecting Ark MCP](setup/install.md), ask your agent:

```text
Use Seedream 5.5 to create one 2K PNG of embroidered heart and daisy
patches with raised stitching and off-white borders. Use a transparent
background and enable the watermark. Show me the result and a download
link. If Seedream 5.5 is unavailable, stop and explain what is missing.
```

**Generation may incur charges.** Review the proposed operation before
approving it. A transparent PNG can be generated from text alone, or from
reference images that do not already contain transparent pixels.

For ordinary image editing, attach the source and describe the change.
The tool's `images` field accepts references; optional `point` or `bbox`
coordinates target the first image on a normalized 0-999 scale.

## Preview Without Generating

The following is an `ark_job_submit` argument object. It previews the same
request controls as the transparent-generation API example without making a
generation call:

```json
{
  "input": {
    "tool_name": "seedream_generate_image",
    "arguments": {
      "input": {
        "model": "dola-seedream-5-5-261028",
        "prompt": "Embroidered heart and daisy patches with raised stitching and off-white borders, transparent background.",
        "size": "2K",
        "response_format": "url",
        "output_format": "png",
        "background": "transparent",
        "watermark": true,
        "persist": true,
        "dry_run": true
      }
    }
  }
}
```

Poll the returned job with `ark_job_get`. Inspect terminal status and
`result.is_error`, then read `result.structured_content.preview`.
Its cost is an estimate, not a bill or a provider quote.
Only change `dry_run` to `false` after approving a paid generation.

The adapter sends `POST /api/v3/images/generations` to the configured ModelArk
host with Bearer authentication. It adds `stream: false`; `persist`, `dry_run`
and the job envelope are MCP controls, not provider payload fields.
Keep the API key in the server's private configuration, never in a tool prompt.

## Limits and Defaults

| Control | Seedream 5.5 |
| --- | --- |
| Named sizes | `1K`, `1.5K`, `2K`; provider default `2K` |
| Custom size | Positive `WIDTHxHEIGHT`; 921,600-4,624,220 pixels inclusive; width/height ratio 1/16-16 |
| Output | PNG by default; explicit `png` or `jpeg` |
| Background | `auto`, `opaque`, `transparent`; transparent requires PNG |
| References | Up to 10 images |
| Variations | 1-10 independent provider calls; no provider batch with `max_images > 1` |
| Prompt optimization | `standard` only; the 5.0 Pro operator setting does not apply |

JPEG is always opaque, even with `background="auto"`. Base64 response delivery
requires `persist=true`. For all fields and output types, see the
[tool reference](/ark-mcp/tool-reference/seedream-generate-image/) and
[model capability registry](models.md#image-capabilities-imagecapabilities).

## Check the Output

Delivery is not visual acceptance. Inspect `item_errors`, or per-variation
errors, and each artifact's `persistence_error`. Provider URLs expire; retain
durable artifacts and download important results.

`generations[].images[].inspection` separately reports decoded dimensions,
format and `has_transparency`. Actual alpha below 255 is different from a PNG
file extension or a provider claim. Inspection is advisory and never triggers
a paid retry; URL-only delivery can leave inspection null. Review the image
visually for text, composition and editing accuracy.

See [image inspection](artifacts.md#seedream-image-inspection) and the
[bounded review/edit workflow](integration-guide.md#seedream-review-and-edit).
After a timeout, recover the existing job rather than submitting again.

## Scope Boundary

**Layer decomposition and native rich-text OCR are not supported by this MCP
integration.** In particular, `layer_decomposition` and
`layer_decomposition_options` are rejected, even though the provider's
Seedream 5.5 API has a layer-decomposition example. Ordinary reference edits
or bounding-box prompts do not enable that separate operation.

## Existing Installations

The built-in image default is 5.5; explicit operator defaults remain unchanged.
An older-model-only bindings list needs either an explicit older default or a
5.5 binding. Custom endpoint IDs must bind to family `seedream_5_5`.
See the [default upgrade note](configuration.md#seedream-default-model-upgrade-note).

Seedream 5.0 Pro remains selectable when configured. Its transparency mode
still requires exactly one already-transparent PNG; it does not acquire 5.5's
text-to-transparent behavior.
