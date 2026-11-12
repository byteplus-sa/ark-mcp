import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { test } from "node:test";
import { toolPage } from "./docs.mjs";
import { toolReferenceGroups } from "./navigation.mjs";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
const model = "dola-seedream-5-5-261028";
const route = "/ark-mcp/tool-reference/seedream-generate-image/";
const tool = (name) => ({
  name,
  description: "Create media.",
  background: "required",
  registration_gate: "Requires BYTEPLUS_MODELARK_API_KEY.",
  scope: null,
  inputSchema: { properties: {} },
  outputSchema: { properties: {} },
});

test("Seedream tool reference includes model guidance and a safe image preview", () => {
  const page = toolPage(tool("seedream_generate_image"));
  const examples = [...page.matchAll(/```json\n([\s\S]*?)\n```/g)].map(
    (match) => JSON.parse(match[1]),
  );
  const job = examples.find((example) => example.name === "ark_job_submit")
    .arguments.input;
  assert.equal(job.tool_name, "seedream_generate_image");
  assert.equal(job.arguments.input.model, model);
  assert.equal(job.arguments.input.background, "transparent");
  assert.equal(job.arguments.input.output_format, "png");
  assert.equal(job.arguments.input.dry_run, true);
  assert.match(page, /id="seedream-5-5"/);
  assert.match(page, /## Seedream 5\.5/);
  assert.match(page, /layer_decomposition/);
  assert.match(page, /not supported/i);
  assert.match(page, /account access/i);
  assert.match(page, /may incur charges/i);
  assert.match(page, /has_transparency/);
  assert.match(page, /921,600-4,624,220/);
  assert.match(page, /already-transparent PNG/);
});

test("Seedream guidance stays scoped to its tool within Images and video", () => {
  const tools = [
    tool("seedream_generate_image"),
    tool("seedance_create_task"),
    tool("ark_list_models"),
  ];
  const group = toolReferenceGroups(tools).find(
    (group) => group.label === "Images and video",
  );
  assert.deepEqual(
    group.items.map((item) => item.slug),
    [
      "tool-reference/seedream-generate-image",
      "tool-reference/seedance-create-task",
    ],
  );
  for (const name of [
    "seedance_create_task",
    "ark_list_models",
    "unknown_tool",
  ]) {
    assert.doesNotMatch(toolPage(tool(name)), /## Seedream 5\.5/);
  }
  assert.deepEqual(toolReferenceGroups([]), []);
});

test("entry points use the tool reference instead of a standalone guide", () => {
  assert.equal(
    existsSync(new URL("../../seedream-5-5.md", import.meta.url)),
    false,
  );
  assert.doesNotMatch(read("../astro.config.mjs"), /slug: "seedream-5-5"/);
  for (const path of [
    "../../index.mdx",
    "../../capabilities.md",
    "../../setup/first-run.md",
    "../../models.md",
    "../../../README.md",
    "../../../.agents/skills/ark-mcp/SKILL.md",
  ]) {
    const content = read(path);
    assert.ok(content.includes(route), path);
    assert.doesNotMatch(content, /seedream-5-5\/|seedream-5-5\.md/);
  }
  assert.ok(read("../astro.config.mjs").includes(`${route}#seedream-5-5`));
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
