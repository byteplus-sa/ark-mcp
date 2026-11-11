import assert from "node:assert/strict";
import { test } from "node:test";
import {
  handbookPage,
  resolveDocLink,
  schemaTable,
  toolPage,
} from "./docs.mjs";

test("handbook metadata links to canonical sources and preserves explicit overrides", () => {
  const page = handbookPage(
    "integrations/codex.md",
    "---\ntitle: Codex\n---\n\n__TOOL_COUNT__ tools",
    39,
  );
  assert.match(page, /edit\/main\/docs\/integrations\/codex.md/);
  assert.match(page, /39 tools/);
  assert.equal(
    handbookPage("integrations/codex.md", page.replaceAll("\n", "\r\n"), 39),
    page,
  );
  const error = "---\ntitle: Not Found\neditUrl: false\n---";
  assert.equal(handbookPage("404.md", error, 39), error);
  const plain = handbookPage("tools.md", "# Tools\n\nUse tools.", 39);
  assert.match(plain, /title: "Tools"/);
  assert.doesNotMatch(plain, /# Tools/);
});

test("documentation links retain the project base and section fragments", () => {
  assert.equal(
    resolveDocLink("setup/updating.md", "../migration-0.2.md"),
    "/ark-mcp/migration-02/",
  );
  assert.equal(
    resolveDocLink("integrations/codex.md", "../configuration.md#providers"),
    "/ark-mcp/configuration/#providers",
  );
  assert.equal(
    resolveDocLink("setup/install.md", "../integrations/index.md"),
    "/ark-mcp/integrations/",
  );
  assert.equal(
    resolveDocLink(
      "getting-started.md",
      "integration-guide.md?view=1#background-execution",
    ),
    "/ark-mcp/integration-guide/?view=1#background-execution",
  );
});

test("external URLs, fragment links and source references are preserved correctly", () => {
  assert.equal(
    resolveDocLink("tools.md", "https://example.com/reference.md"),
    "https://example.com/reference.md",
  );
  assert.equal(resolveDocLink("tools.md", "#output"), "#output");
  assert.equal(
    resolveDocLink("getting-started.md", "../.agents/skills/ark-mcp/SKILL.md"),
    "https://github.com/byteplus-sa/ark-mcp/blob/main/.agents/skills/ark-mcp/SKILL.md",
  );
  assert.throws(
    () => resolveDocLink("tools.md", "../../../private.md"),
    /outside repository/,
  );
});

test("field tables describe required fields, choices, constraints and defaults", () => {
  const table = schemaTable({
    type: "object",
    required: ["prompt"],
    properties: {
      prompt: {
        type: "string",
        minLength: 1,
        description: "Text | instruction",
      },
      mode: {
        type: "string",
        enum: ["a", "b"],
        default: "a",
        description: "Mode",
      },
      detail: { $ref: "#/$defs/Detail" },
    },
  });
  assert.match(table, /prompt.*Yes.*Text \\?\| instruction.*minLength: 1/);
  assert.match(table, /mode.*a.*b.*No.*Default/);
  assert.match(table, /Detail/);
  assert.equal(
    schemaTable({ type: "object", properties: {} }),
    "No fields. Pass `{}`.\n",
  );
});

test("required background tools show the complete ordinary job wrapper", () => {
  const page = toolPage({
    name: "seedream_generate_image",
    description: "Generate an image.",
    background: "required",
    registration_gate: "Requires BYTEPLUS_MODELARK_API_KEY.",
    scope: "seed:image:generate",
    inputSchema: {
      properties: { input: { $ref: "#/$defs/Input" } },
      $defs: { Input: { properties: { prompt: { type: "string" } } } },
    },
    outputSchema: { properties: { images: { type: "array" } } },
  });
  assert.match(page, /ark_job_submit/);
  assert.match(page, /"arguments": \{\s*"input":/);
  assert.match(page, /"dry_run": true/);
  assert.match(page, /result\.structured_content/);
  assert.match(page, /seed:image:generate/);
  assert.match(page, /Requires BYTEPLUS_MODELARK_API_KEY/);
  assert.match(page, /Ask your connected AI client/);
  assert.match(
    page,
    /<details>\s*<summary>Exact MCP call \(for developers\)<\/summary>/,
  );
});

test("foreground tools do not advise submission through background jobs", () => {
  const page = toolPage({
    name: "ark_job_capabilities",
    description: "Discover jobs.",
    background: null,
    registration_gate: "Always registered.",
    scope: null,
    inputSchema: { properties: {} },
    outputSchema: { properties: {} },
  });
  assert.match(page, /Foreground/);
  assert.match(page, /"arguments": \{\}/);
  assert.doesNotMatch(page, /"tool_name": "ark_job_capabilities"/);
  assert.doesNotMatch(page, /An Ark job ID is not a provider task ID/);
});
