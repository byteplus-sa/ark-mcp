import assert from "node:assert/strict";
import {
  cpSync,
  existsSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve, sep } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const repository = fileURLToPath(new URL("../../../", import.meta.url));
const skill = join(repository, ".agents/skills/ark-mcp");
const readDoc = (path) => readFileSync(join(repository, "docs", path), "utf8");

test("the complete skill keeps its local references after standalone installation", () => {
  const temporary = mkdtempSync(join(tmpdir(), "ark-skill-install-"));
  const installed = join(temporary, "ark-mcp");
  try {
    cpSync(skill, installed, {
      recursive: true,
      errorOnExist: true,
      force: false,
    });
    const files = readdirSync(skill, { recursive: true, withFileTypes: true })
      .filter((entry) => entry.isFile())
      .map((entry) =>
        join(entry.parentPath, entry.name).slice(skill.length + 1),
      );
    assert.ok(files.includes("SKILL.md"));
    assert.ok(files.includes(join("agents", "openai.yaml")));
    assert.ok(files.some((path) => path.startsWith(`references${sep}`)));
    for (const path of files) {
      const contents = readFileSync(join(installed, path));
      assert.deepEqual(contents, readFileSync(join(skill, path)));
      if (!path.endsWith(".md")) continue;
      for (const [, target] of contents
        .toString()
        .matchAll(/\[[^\]]*\]\(([^)\s]+)\)/g)) {
        if (/^(?:[a-z][a-z\d+.-]*:|#)/i.test(target)) continue;
        const destination = resolve(
          dirname(join(installed, path)),
          target.split("#")[0],
        );
        assert.ok(
          destination.startsWith(`${installed}${sep}`),
          `${path}: reference escapes the installed skill: ${target}`,
        );
        assert.ok(existsSync(destination), `${path}: missing ${target}`);
      }
    }
  } finally {
    rmSync(temporary, { recursive: true, force: true });
  }
});

test("skill installation is discoverable and uses agent-native paths", () => {
  const guide = readDoc("setup/agent-skill.md");
  assert.match(readDoc("setup/install.md"), /agent-skill\.md/);
  assert.match(readDoc("site/astro.config.mjs"), /slug: "setup\/agent-skill"/);
  assert.match(
    guide,
    /https:\/\/github\.com\/byteplus-sa\/ark-mcp\/tree\/main\/\.agents\/skills\/ark-mcp/,
  );
  for (const path of [
    "~/.agents/skills/ark-mcp/",
    "~/.claude/skills/ark-mcp/",
    ".agents/skills/ark-mcp/",
    ".claude/skills/ark-mcp/",
  ]) {
    assert.ok(guide.includes(path), path);
  }
  assert.match(guide, /references\/.*agents\//s);
  assert.match(guide, /already exists.*ask/s);
  assert.match(guide, /Do not.*credentials/s);
  assert.doesNotMatch(guide, /```(?:bash|sh|powershell|json|toml)\b/);
});

test("skill environment guidance defers setup to the private-file handbook", () => {
  const environment = readFileSync(
    join(skill, "references/environment.md"),
    "utf8",
  );
  assert.doesNotMatch(environment, /Copy `\.env\.example` to `\.env`/);
  assert.match(
    environment,
    /https:\/\/byteplus-sa\.github\.io\/ark-mcp\/setup\/install\//,
  );
  assert.match(environment, /ARK_MCP_ENV_FILE/);
});
