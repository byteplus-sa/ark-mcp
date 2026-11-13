import assert from "node:assert/strict";
import { test } from "node:test";
import { toolReferenceGroups } from "./navigation.mjs";

test("audio, understanding, 3D and avatars have separate tool sections", () => {
  const names = [
    "seed_audio_generate",
    "speech_to_text",
    "seed_understand",
    "seed_audio_understand",
    "hyper3d_create_task",
    "hitem3d_create_task",
    "omnihuman_create_task",
    "omnihuman_get_task",
    "omnihuman_cancel_task",
  ];
  const groups = toolReferenceGroups(names.map((name) => ({ name })));
  const entries = Object.fromEntries(
    groups.map((group) => [group.label, group.items.map((item) => item.label)]),
  );
  assert.deepEqual(entries.Audio, ["seed_audio_generate", "speech_to_text"]);
  assert.deepEqual(entries.Understanding, [
    "seed_understand",
    "seed_audio_understand",
  ]);
  assert.deepEqual(entries["3D"], [
    "hyper3d_create_task",
    "hitem3d_create_task",
  ]);
  assert.deepEqual(entries.Avatars, [
    "omnihuman_create_task",
    "omnihuman_get_task",
    "omnihuman_cancel_task",
  ]);
  assert.equal(groups.flatMap((group) => group.items).length, names.length);
  assert.ok(groups.every((group) => group.collapsed));
});

test("tool routes and exact MCP names remain unchanged", () => {
  const groups = toolReferenceGroups([{ name: "seed_audio_understand" }]);
  assert.deepEqual(groups[0].items, [
    {
      label: "seed_audio_understand",
      slug: "tool-reference/seed-audio-understand",
    },
  ]);
});

test("empty catalogs do not create empty navigation groups", () => {
  assert.deepEqual(toolReferenceGroups([]), []);
});

test("uncategorized tools fail visibly instead of disappearing from navigation", () => {
  assert.throws(
    () => toolReferenceGroups([{ name: "new_capability" }]),
    /Uncategorized tool: new_capability/,
  );
});

test("duplicate catalog entries cannot create duplicate navigation links", () => {
  assert.throws(
    () =>
      toolReferenceGroups([
        { name: "seed_audio_generate" },
        { name: "seed_audio_generate" },
      ]),
    /Duplicate tool: seed_audio_generate/,
  );
});
