const categories = [
  [
    "Discovery and jobs",
    (name) => name.startsWith("ark_job_") || name === "ark_list_models",
  ],
  [
    "Images and video",
    (name) =>
      name.startsWith("seedream_") ||
      name.startsWith("seedance_") ||
      name.startsWith("gen_task_"),
  ],
  ["Audio", (name) => ["seed_audio_generate", "speech_to_text"].includes(name)],
  [
    "Understanding",
    (name) => ["seed_understand", "seed_audio_understand"].includes(name),
  ],
  ["3D", (name) => name.startsWith("hyper3d_") || name.startsWith("hitem3d_")],
  ["Avatars", (name) => name.startsWith("omnihuman_")],
  ["VOD AI MediaKit", (name) => name.startsWith("vod_")],
  ["Asset library", (name) => name.startsWith("ark_asset_")],
  [
    "Uploads and artifacts",
    (name) => name.startsWith("media_") || name.startsWith("seed_media_"),
  ],
];

export function toolReferenceGroups(tools) {
  const names = new Set();
  for (const { name } of tools) {
    if (names.has(name)) throw new Error(`Duplicate tool: ${name}`);
    names.add(name);
    const matches = categories.filter(([, accepts]) => accepts(name));
    if (matches.length !== 1) throw new Error(`Uncategorized tool: ${name}`);
  }
  return categories
    .map(([label, accepts]) => ({
      label,
      collapsed: true,
      items: tools
        .filter((tool) => accepts(tool.name))
        .map((tool) => ({
          label: tool.name,
          slug: `tool-reference/${tool.name.replaceAll("_", "-")}`,
        })),
    }))
    .filter((group) => group.items.length);
}
