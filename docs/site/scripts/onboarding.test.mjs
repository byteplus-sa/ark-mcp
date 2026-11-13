import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const page = (path) =>
  readFileSync(new URL(`../../${path}`, import.meta.url), "utf8");

test("primary onboarding offers guided setup without shell commands", () => {
  for (const path of ["setup/install.md", "getting-started.md"]) {
    const content = page(path);
    assert.doesNotMatch(content, /```(?:bash|sh|shell|powershell|json|toml)\b/);
    assert.match(content, /manual-install\.md/);
  }
  const install = page("setup/install.md");
  assert.match(install, /terminal.*file/i);
  assert.match(install, /not.*one-click installer/i);
  assert.match(install, /shared-server\.md/);
});

test("first use leads with a generation prompt rather than tool orchestration", () => {
  const content = page("setup/first-run.md");
  const prompts = [...content.matchAll(/```text\n([\s\S]*?)\n```/g)].map(
    (match) => match[1],
  );
  assert.ok(prompts.length >= 2);
  assert.match(prompts[0], /generate one image/i);
  assert.match(prompts[0], /red ceramic cup/i);
  assert.match(prompts[0], /download link/i);
  for (const prompt of prompts) {
    assert.doesNotMatch(
      prompt,
      /ark_job_|ark_list_models|seedream_generate_image|seedance_create_task|gen_task_|dry_run|structured_content|poll_after_ms/,
    );
  }
  assert.doesNotMatch(content, /```(?:json|bash|sh|powershell|toml)\b/);
  assert.match(content, /may incur charges/i);
  assert.match(content, /Do not submit a new generation request/);
  assert.match(content, /preview.*not.*generated image/i);
  assert.match(content, /integration-guide\.md#background-execution/);
  for (const anchor of [
    "discover-before-spending",
    "preview-an-image-request",
    "retrieve-the-job-result",
    "generate-deliberately",
    "video-and-other-provider-tasks",
  ]) {
    assert.ok(
      content.includes(`id="${anchor}"`) ||
        [...content.matchAll(/^## (.+)$/gm)].some(
          (match) => match[1].toLowerCase().replaceAll(" ", "-") === anchor,
        ),
      anchor,
    );
  }
});

test("client guides put optional manual configuration behind disclosure", () => {
  for (const client of [
    "claude-code",
    "claude-desktop",
    "codex",
    "trae",
    "cursor",
    "vscode",
    "opencode",
  ]) {
    const content = page(`integrations/${client}.md`);
    const start = content.indexOf("<details>");
    const end = content.indexOf("</details>", start);
    assert.ok(start > 0 && end > start, client);
    assert.match(content.slice(0, start), /guided setup/i, client);
    assert.doesNotMatch(
      content.slice(0, start),
      /```(?:bash|json|toml|powershell)\b/,
      client,
    );
    for (const match of content.matchAll(
      /```(?:bash|json|toml|powershell)\b/g,
    )) {
      assert.ok(match.index > start && match.index < end, client);
    }
  }
});

test("manual fallback selects a reviewed release and keeps private launch contract", () => {
  const manual = page("setup/manual-install.md");
  assert.match(manual, /latest published non-draft, non-prerelease/);
  assert.match(manual, /git switch --detach v0\.3\.0/);
  assert.match(manual, /do not silently fall back to main/);
  assert.match(manual, /record its tag and commit SHA/);
  assert.match(manual, /uv sync --locked --no-dev/);
  assert.match(manual, /ARK_MCP_ENV_FILE/);
  assert.match(manual, /--no-sync/);
  assert.match(manual, /v0\.2\.0/);
});

test("guided setup distinguishes the setup skill from the usage skill", () => {
  const install = page("setup/install.md");
  assert.match(install, /\/\.agents\/skills\/ark-mcp-setup/);
  assert.match(install, /--skill ark-mcp-setup/);
  assert.match(install, /separate from authorizing it/);
  assert.match(install, /Published `v0\.3\.0` includes/);
});

test("legacy onboarding does not advise inline provider keys or new dependency edits", () => {
  const gettingStarted = page("getting-started.md");
  const integrationGuide = page("integration-guide.md");
  const troubleshooting = page("troubleshooting.md");
  assert.doesNotMatch(
    gettingStarted + integrationGuide,
    /Inline in the client config|export BYTEPLUS_.*API_KEY|cp \.env\.example/,
  );
  assert.doesNotMatch(troubleshooting, /uv add truststore|ps -eo/);
});
