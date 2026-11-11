import { relative } from "node:path";
import { fileURLToPath } from "node:url";
import { resolveDocLink } from "./docs.mjs";

const generated = fileURLToPath(
  new URL("../src/content/docs/", import.meta.url),
);

export default function remarkLinks() {
  return (tree, file) => {
    const source = relative(generated, file.path).replaceAll("\\", "/");
    function walk(node) {
      if ((node.type === "link" || node.type === "definition") && node.url) {
        node.url = resolveDocLink(source, node.url);
      }
      for (const child of node.children || []) walk(child);
    }
    walk(tree);
  };
}
