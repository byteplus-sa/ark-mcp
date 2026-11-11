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

test("manual fallback retains reviewed version and private launch contract", () => {
  const manual = page("setup/manual-install.md");
  assert.match(manual, /137a0c63c00be37b6d1e9bd9c021d3fd011d50d7/);
  assert.match(manual, /uv sync --locked --no-dev/);
  assert.match(manual, /ARK_MCP_ENV_FILE/);
  assert.match(manual, /--no-sync/);
  assert.match(manual, /v0\.2\.0/);
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
