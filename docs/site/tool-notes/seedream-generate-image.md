<span id="seedream-5-5"></span>

## Seedream 5.5

The built-in image default is **`dola-seedream-5-5-261028`**. The same
`seedream_generate_image` tool handles text generation, reference editing,
point/bbox editing and independent variations. Explicit operator defaults
remain unchanged; Seedream 5.0 Pro is still selectable when configured.

This reference describes the server revision built with this site. Older
installations, including `v0.2.0`, may not include 5.5. Follow
[Update and Roll Back](/ark-mcp/setup/updating/) to select a reviewed revision.
Discovery shows configured models, not provider account access.

### Transparent Images and Editing

A transparent PNG can be generated from text alone or from references without
existing alpha. Ask your connected agent:

```text
Use Seedream 5.5 to create one 2K PNG of embroidered heart and daisy
patches with raised stitching and off-white borders. Use a transparent
background and enable the watermark. Show me the result and a download
link. If Seedream 5.5 is unavailable, stop and explain what is missing.
```

Generation may incur charges. The exact MCP call above previews a transparent
PNG with `dry_run=true`, without generating or billing. Inspect
`result.structured_content.preview`; its cost is an estimate, not a bill.
Only change `dry_run` to `false` after approving a paid generation.

For editing, attach the source and describe the change. `images` accepts up to
ten references. Optional `point` or `bbox` coordinates target the first image
on a normalized 0-999 scale. Ordinary reference edits and region prompts do
not enable layer decomposition.

### Model Limits

| Control | Seedream 5.5 |
| --- | --- |
| Named sizes | `1K`, `1.5K`, `2K`; provider default `2K` |
| Custom size | Positive `WIDTHxHEIGHT`; 921,600-4,624,220 pixels inclusive; width/height ratio 1/16-16 |
| Output | PNG by default; explicit `png` or `jpeg` |
| Background | `auto`, `opaque`, `transparent`; transparent requires PNG |
| References | Up to 10 images |
| Variations | 1-10 independent provider calls; no provider batch with `max_images > 1` |
| Prompt optimization | `standard` only; the Pro operator setting does not apply |
| Safety identifier | Optional stable hashed ID; 1-64 non-space printable ASCII characters; redacted from previews and structured logs |

JPEG is always opaque, including with `background="auto"`. Base64 delivery
requires `persist=true`. Pro transparency still requires exactly one
already-transparent PNG; it does not gain text-to-transparent generation.
Custom endpoint IDs must bind to family `seedream_5_5`. See the
[model capabilities](/ark-mcp/models/#image-capabilities-imagecapabilities)
and [default upgrade note](/ark-mcp/configuration/#seedream-default-model-upgrade-note).

The adapter sends `POST /api/v3/images/generations` with Bearer authentication
and `stream: false`. `persist`, `dry_run` and the job envelope are MCP controls,
not provider payload fields. Keep keys in private server configuration.

### Output Checks and Scope

Delivery is not visual acceptance. Inspect `item_errors`, per-variation errors
and artifact `persistence_error`. Provider URLs expire; retain durable artifacts.
`generations[].images[].inspection` separately reports decoded dimensions,
format and `has_transparency`. Actual alpha below 255 differs from a PNG file
extension or a provider claim. Inspection is advisory and never triggers a
paid retry; URL-only delivery can leave inspection null.

Review text, composition and edit accuracy visually. See
[image inspection](/ark-mcp/artifacts/#seedream-image-inspection) and the
[bounded review/edit workflow](/ark-mcp/integration-guide/#seedream-review-and-edit).
After a timeout, recover the existing job instead of submitting again.

**Layer decomposition and native rich-text OCR are not supported by this MCP
integration.** `layer_decomposition` and `layer_decomposition_options` are
rejected even though the provider API documents the separate operation.
