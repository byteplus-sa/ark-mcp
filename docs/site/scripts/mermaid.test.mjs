import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { createMarkdownProcessor, unified } from "@astrojs/markdown-remark";
import mermaid from "astro-mermaid";
import remarkLinks from "./remark-links.mjs";
import rehypeTables from "./rehype-tables.mjs";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

test("the site registers a bundled, strict Mermaid renderer before Starlight", () => {
  const config = read("../astro.config.mjs");
  assert.match(config, /import mermaid from "astro-mermaid"/);
  assert.ok(config.indexOf("mermaid({") < config.indexOf("starlight({"));
  assert.match(config, /autoTheme: true/);
  assert.match(config, /enableLog: false/);
  assert.match(config, /securityLevel: "strict"/);
  const dependencies = JSON.parse(read("../package.json")).dependencies;
  assert.ok(dependencies["astro-mermaid"]);
  assert.ok(dependencies.mermaid);
});

test("handbook architecture diagrams retain editable Mermaid fences", () => {
  for (const path of [
    "../../self-hosting.md",
    "../../architecture.md",
    "../../s3-object-storage.md",
  ]) {
    assert.match(read(path), /```mermaid\n/);
  }
});

test("Mermaid transforms real fences without losing links, tables or ordinary code", async () => {
  let processor;
  const scripts = [];
  const warnings = [];
  await mermaid({
    enableLog: false,
    mermaidConfig: { securityLevel: "strict" },
  }).hooks["astro:config:setup"]({
    config: {
      root: new URL("../", import.meta.url),
      markdown: {
        processor: unified({
          remarkPlugins: [remarkLinks],
          rehypePlugins: [rehypeTables],
        }),
      },
    },
    updateConfig: (config) => {
      processor = config.markdown?.processor;
    },
    injectScript: (_stage, script) => scripts.push(script),
    addWatchFile: () => {},
    logger: { info: () => {}, warn: (message) => warnings.push(message) },
    command: "build",
  });
  assert.deepEqual(warnings, []);
  assert.ok(processor);
  const renderer = await createMarkdownProcessor(processor.options);
  const source = [
    '```mermaid\nflowchart LR\nA["<script>alert(1)</script>"] --> B["Server"]\n```',
    "[Configuration](configuration.md)",
    "| Column |\n| --- |\n| Value |",
    "```text\nOrdinary code\n```",
  ].join("\n\n");
  const { code } = await renderer.render(source, {
    fileURL: new URL("../src/content/docs/self-hosting.md", import.meta.url),
    filePath: fileURLToPath(
      new URL("../src/content/docs/self-hosting.md", import.meta.url),
    ),
  });
  assert.match(code, /<pre class="mermaid">/);
  assert.match(code, /(?:&lt;|&#x3[cC];)script(?:&gt;|>)/);
  assert.doesNotMatch(code, /<script>alert/);
  assert.match(code, /href="\/ark-mcp\/configuration\/"/);
  assert.match(code, /class="table-scroll"/);
  assert.match(code, /Ordinary code/);
  assert.ok(scripts.some((script) => script.includes("import('mermaid')")));
  assert.ok(
    scripts.some((script) => script.includes('"securityLevel":"strict"')),
  );
});
