import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { resolveDocLink } from "./docs.mjs";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
const model = "dola-seedream-5-5-261028";

test("Seedream 5.5 guide provides a safe transparent-image job preview", () => {
  const guide = read("../../seedream-5-5.md");
  const examples = [...guide.matchAll(/```json\n([\s\S]*?)\n```/g)].map(
    (match) => JSON.parse(match[1]),
  );
  assert.equal(examples.length, 1);
  const job = examples[0].input;
  assert.equal(job.tool_name, "seedream_generate_image");
  assert.deepEqual(job.arguments.input, {
    model,
    prompt:
      "Embroidered heart and daisy patches with raised stitching and off-white borders, transparent background.",
    size: "2K",
    response_format: "url",
    output_format: "png",
    background: "transparent",
    watermark: true,
    persist: true,
    dry_run: true,
  });
  assert.match(guide, /layer_decomposition/);
  assert.match(guide, /not supported/i);
  assert.match(guide, /account access/i);
  assert.match(guide, /may incur charges/i);
  assert.match(guide, /has_transparency/);
});

test("Seedream 5.5 guide is reachable from navigation and capability entry points", () => {
  assert.match(read("../astro.config.mjs"), /slug: "seedream-5-5"/);
  assert.match(read("../../index.mdx"), /\/ark-mcp\/seedream-5-5\//);
  assert.match(read("../../capabilities.md"), /seedream-5-5\.md/);
  assert.match(read("../../setup/first-run.md"), /seedream-5-5\.md/);
  assert.equal(
    resolveDocLink("models.md", "seedream-5-5.md"),
    "/ark-mcp/seedream-5-5/",
  );
});

test("generated Seedream tool example previews the launch model without billing", () => {
  const examples = JSON.parse(read("../tool-examples.json"));
  const input = examples.seedream_generate_image.input;
  assert.equal(input.model, model);
  assert.equal(input.background, "transparent");
  assert.equal(input.output_format, "png");
  assert.equal(input.size, "2K");
  assert.equal(input.dry_run, true);
  assert.equal("layer_decomposition" in input, false);
});
