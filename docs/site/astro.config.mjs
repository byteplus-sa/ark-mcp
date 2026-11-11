import { readFileSync } from "node:fs";
import { defineConfig } from "astro/config";
import starlight from "@astrojs/starlight";
import { unified } from "@astrojs/markdown-remark";
import starlightLinksValidator from "starlight-links-validator";
import remarkLinks from "./scripts/remark-links.mjs";

const { tools } = JSON.parse(
  readFileSync(new URL("./src/data/tools.json", import.meta.url), "utf8"),
);
const groups = [
  [
    "Discovery and background jobs",
    (name) => name.startsWith("ark_job_") || name === "ark_list_models",
  ],
  [
    "Images and video",
    (name) =>
      name.startsWith("seedream_") ||
      name.startsWith("seedance_") ||
      name.startsWith("gen_task_"),
  ],
  [
    "Audio and understanding",
    (name) =>
      [
        "seed_audio_generate",
        "seed_understand",
        "seed_audio_understand",
        "speech_to_text",
      ].includes(name),
  ],
  [
    "3D and avatars",
    (name) =>
      name.startsWith("hyper3d_") ||
      name.startsWith("hitem3d_") ||
      name.startsWith("omnihuman_"),
  ],
  ["VOD AI MediaKit", (name) => name.startsWith("vod_")],
  ["Asset library", (name) => name.startsWith("ark_asset_")],
  [
    "Uploads and artifacts",
    (name) => name.startsWith("media_") || name.startsWith("seed_media_"),
  ],
];

export default defineConfig({
  site: "https://byteplus-sa.github.io",
  base: "/ark-mcp",
  trailingSlash: "always",
  outDir: "../../out/docs-site",
  markdown: { processor: unified({ remarkPlugins: [remarkLinks] }) },
  integrations: [
    starlight({
      title: "BytePlus Ark MCP",
      description:
        "Set up Ark MCP in your AI client and use BytePlus image, video, audio, understanding and media tools.",
      social: [
        {
          icon: "github",
          label: "GitHub",
          href: "https://github.com/byteplus-sa/ark-mcp",
        },
      ],
      editLink: {
        baseUrl: "https://github.com/byteplus-sa/ark-mcp/edit/main/docs/",
      },
      customCss: ["./src/styles/custom.css"],
      plugins: [starlightLinksValidator({ errorOnRelativeLinks: false })],
      expressiveCode: {
        themes: ["vitesse-black", "vitesse-light"],
        styleOverrides: {
          borderRadius: "8px",
          borderColor: "var(--sl-color-hairline)",
        },
      },
      sidebar: [
        {
          label: "Get Started",
          items: [
            { slug: "setup/install", label: "Install Ark MCP" },
            { slug: "api-keys", label: "Provider Keys" },
            { slug: "setup/first-run", label: "Your First Tool Call" },
            { slug: "setup/updating", label: "Update and Roll Back" },
          ],
        },
        {
          label: "Client Integrations",
          items: [
            { slug: "integrations", label: "Choose Your Client" },
            ...[
              "claude-desktop",
              "claude-code",
              "codex",
              "trae",
              "cursor",
              "vscode",
              "opencode",
              "python",
            ].map((client) => ({ slug: `integrations/${client}` })),
            { slug: "transports", label: "HTTP and Shared Servers" },
          ],
        },
        {
          label: "Tool Reference",
          collapsed: true,
          items: [
            { slug: "capabilities", label: "Choose a Tool" },
            ...groups.map(([label, matches]) => ({
              label,
              collapsed: true,
              items: tools
                .filter((tool) => matches(tool.name))
                .map((tool) => ({
                  label: tool.name,
                  slug: `tool-reference/${tool.name.replaceAll("_", "-")}`,
                })),
            })),
          ],
        },
        {
          label: "Workflows and Concepts",
          collapsed: true,
          items: [
            { slug: "models" },
            { slug: "tools", label: "Worked Tool Examples" },
            { slug: "artifacts" },
            { slug: "multi-generation" },
            { slug: "assets" },
            { slug: "use-cases" },
            { slug: "integration-guide", label: "Background Execution" },
          ],
        },
        {
          label: "Configuration and Operations",
          collapsed: true,
          items: [
            "configuration",
            "self-hosting",
            "deployment",
            "runtime",
            "security",
            "observability",
            "s3-object-storage",
            "architecture",
            "api-reference",
            "releases",
            "changelog",
            "migration-02",
            "site-maintenance",
          ].map((slug) => ({ slug })),
        },
        { slug: "troubleshooting" },
      ],
    }),
  ],
});
