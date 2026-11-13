import assert from "node:assert/strict";
import { test } from "node:test";
import rehypeTables from "./rehype-tables.mjs";

test("tables retain their semantics inside keyboard-accessible scroll regions", () => {
  const table = {
    type: "element",
    tagName: "table",
    properties: { id: "inputs" },
    children: [{ type: "element", tagName: "tbody", children: [] }],
  };
  const tree = { type: "root", children: [table] };
  rehypeTables()(tree);
  assert.deepEqual(tree.children[0].properties, {
    className: ["table-scroll"],
    tabIndex: 0,
    role: "region",
    ariaLabel: "Scrollable table",
  });
  assert.equal(tree.children[0].children[0], table);
  assert.equal(table.properties.id, "inputs");
});

test("nested schema tables are wrapped once and other content is unchanged", () => {
  const text = { type: "text", value: "No table here" };
  const details = {
    type: "element",
    tagName: "details",
    children: [{ type: "element", tagName: "table", children: [] }],
  };
  const tree = { type: "root", children: [text, details] };
  const transform = rehypeTables();
  transform(tree);
  const once = structuredClone(tree);
  transform(tree);
  assert.deepEqual(tree, once);
  assert.equal(tree.children[0], text);
  assert.equal(details.children[0].tagName, "div");
  assert.equal(details.children[0].children[0].tagName, "table");
});

test("empty trees and leaf nodes remain unchanged", () => {
  for (const tree of [
    { type: "root", children: [] },
    { type: "text", value: "" },
  ]) {
    const original = structuredClone(tree);
    rehypeTables()(tree);
    assert.deepEqual(tree, original);
  }
});
