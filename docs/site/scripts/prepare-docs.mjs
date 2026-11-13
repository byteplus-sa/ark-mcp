import { spawnSync } from "node:child_process";
import { mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { handbookPage, toolPage } from "./docs.mjs";

const site = fileURLToPath(new URL("../", import.meta.url));
const repository = fileURLToPath(new URL("../../../", import.meta.url));
const docs = join(repository, "docs");
const generated = join(site, "src/content/docs");
const python =
  process.env.ARK_DOCS_PYTHON ||
  join(
    repository,
    ".venv",
    process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
  );
const exported = spawnSync(
  python,
  ["-I", "-B", join(repository, "scripts/export_tool_docs.py")],
  {
    cwd: site,
    encoding: "utf8",
    maxBuffer: 32 * 1024 * 1024,
  },
);
if (exported.error || exported.status !== 0) {
  throw new Error(
    `Tool schema export failed. Run uv sync --locked first.\n${exported.error?.message || exported.stderr}`,
  );
}
const data = JSON.parse(exported.stdout);
if (!Array.isArray(data.tools) || !data.tools.length)
  throw new Error("Empty tool catalog.");

async function collect(directory) {
  const files = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    if (entry.name === "site") continue;
    const path = join(directory, entry.name);
    if (entry.isDirectory()) files.push(...(await collect(path)));
    else if (entry.isFile() && /\.mdx?$/.test(entry.name)) files.push(path);
  }
  return files.sort();
}

await rm(generated, { recursive: true, force: true });
await mkdir(generated, { recursive: true });
for (const path of await collect(docs)) {
  const filename = relative(docs, path);
  const content = handbookPage(
    filename,
    await readFile(path, "utf8"),
    data.tools.length,
  );
  const destination = join(generated, filename);
  await mkdir(dirname(destination), { recursive: true });
  await writeFile(destination, content);
}
for (const tool of data.tools) {
  const destination = join(
    generated,
    "tool-reference",
    `${tool.name.replaceAll("_", "-")}.md`,
  );
  await mkdir(dirname(destination), { recursive: true });
  await writeFile(destination, toolPage(tool));
}
await mkdir(join(site, "src/data"), { recursive: true });
await writeFile(
  join(site, "src/data/tools.json"),
  JSON.stringify(data, null, 2) + "\n",
);
console.log(
  `Prepared handbook and ${data.tools.length} tool pages without loading credentials or calling providers.`,
);
