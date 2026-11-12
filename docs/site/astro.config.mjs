import { readFileSync } from "node:fs";
import { defineConfig } from "astro/config";
import starlight from "@astrojs/starlight";
import { unified } from "@astrojs/markdown-remark";
import starlightLinksValidator from "starlight-links-validator";
import remarkLinks from "./scripts/remark-links.mjs";
import { toolReferenceGroups } from "./scripts/navigation.mjs";
import rehypeTables from "./scripts/rehype-tables.mjs";

const { tools } = JSON.parse(
  readFileSync(new URL("./src/data/tools.json", import.meta.url), "utf8"),
);

export default defineConfig({
  site: "https://byteplus-sa.github.io",
  base: "/ark-mcp",
  trailingSlash: "always",
  outDir: "../../out/docs-site",
  markdown: {
    processor: unified({
      remarkPlugins: [remarkLinks],
      rehypePlugins: [rehypeTables],
    }),
  },
  integrations: [
    starlight({
      title: "BytePlus Ark MCP",
      logo: {
        light: "./src/assets/byteplus-mark-light.svg",
        dark: "./src/assets/byteplus-mark-dark.svg",
        alt: "",
      },
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
          codeBackground: "var(--sl-color-gray-6)",
        },
      },
      sidebar: [
        {
          label: "Get Started",
          items: [
            { slug: "setup/install", label: "Set Up Ark MCP" },
            { slug: "setup/agent-skill", label: "Install the Agent Skill" },
            { slug: "setup/shared-server", label: "Use a Shared Server" },
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
            ...toolReferenceGroups(tools),
          ],
        },
        {
          label: "Workflows and Concepts",
          collapsed: true,
          items: [
            { slug: "models" },
            { slug: "seedream-5-5", label: "Seedream 5.5" },
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
            "setup/manual-install",
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
