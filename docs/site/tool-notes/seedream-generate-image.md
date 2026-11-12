This reference describes the server revision built with this site. Older
installations, including `v0.2.0`, may not include 5.5. Follow
[Update and Roll Back](/ark-mcp/setup/updating/) to select a reviewed revision.

### Transparent Images and Editing

A transparent PNG can be generated with 5.5 from text alone or from references
without existing alpha. Ask your connected agent:

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

For editing, attach the source and describe the change. Reference counts are
model-specific, as shown above. Optional `point` or `bbox` coordinates target
the first image on a normalized 0-999 scale. Ordinary reference edits and
region prompts do not enable layer decomposition.

5.5 also accepts a positive custom `WIDTHxHEIGHT`: 921,600-4,624,220 pixels
inclusive, with width/height ratio 1/16-16. Every family supports 1-10 independent
`variations`; only batch-capable families accept `max_images > 1`, and batching
cannot be combined with multiple variations. On 5.5/Pro, optional
`safety_identifier` accepts 1-64 non-space printable ASCII characters; prefer a
stable hash, not personal data. It is redacted from previews and structured logs.

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
