# Documentation Site

The Ark MCP handbook is published through Astro/Starlight. The site framework
lives in `docs/site/`; human-written pages remain in `docs/`, alongside the
existing handbook. Tool pages are generated from the static MCP catalog and
the exact FastMCP/Pydantic input and output schemas.

The unversioned public site tracks the reviewed `main` revision used by its
deployment workflow, not every published tag. Its generated contracts,
runtime guidance, Python example and validation apply to that build revision.
Historical releases can have different dependencies and capabilities. Keep
the [installation revision guidance](setup/manual-install.md#choose-an-installation)
current when a suitable patched release is published.

## Develop Locally

Requirements: the project's locked Python environment and Node.js 22.23.4+
(Node 24 is supported). TypeScript uses the newest supported 6.x release
because the Astro checker currently declares TypeScript 5/6 compatibility.

```bash
uv sync --locked
npm --prefix docs/site ci
npm --prefix docs/site run dev -- --host 127.0.0.1
```

Open the URL printed by Astro, including `/ark-mcp/`. Search is generated for
production builds; use the preview command to test the real search index:

```bash
npm --prefix docs/site test
npm --prefix docs/site run check
npm --prefix docs/site run build
npm --prefix docs/site run preview -- --host 127.0.0.1
```

The static output lives in the gitignored `out/docs-site/`. Generated content
and tool JSON under `docs/site/src/` are also ignored. Never edit generated
pages or commit them. Set `ARK_DOCS_PYTHON` to an absolute interpreter path if
the project environment is not at `.venv/`.

## Content Sources

| Content | Edit here |
| --- | --- |
| Landing page | `docs/index.mdx` |
| Installation and first run | `docs/setup/` |
| Client integrations | `docs/integrations/` |
| Capability overview | `docs/capabilities.md` |
| Existing handbook | Other `docs/*.md` files |
| Tool contracts | Handler docstrings, Pydantic models and `src/ark_mcp/tools/_catalog.py` |
| Supported-model tables | `docs/site/tool-models.json`, rendered once per model-backed tool page by `docs/site/scripts/docs.mjs` |
| Tool-specific usage guidance | `docs/site/tool-notes/`, injected by `docs/site/scripts/docs.mjs` into the relevant tool page only |
| Page generation and link rewriting | `docs/site/scripts/` |
| Navigation and theme | `docs/site/astro.config.mjs`, `docs/site/src/styles/custom.css` |
| Tool-reference categories | `docs/site/scripts/navigation.mjs` |
| BytePlus logo variants | `docs/site/src/assets/byteplus-mark-{light,dark}.svg` |

The primary setup page offers a request for a terminal/file-capable coding
agent and an existing shared-server route. It does not claim a one-click
installer exists. Technical preparation lives in `setup/manual-install.md`;
client-specific fallback snippets remain inside disclosures. Keep README and
older handbook entry points linked to these canonical guides instead of
duplicating commands or credential-bearing client examples.

`onboarding.test.mjs` guards that separation. The first-run page leads with
copyable generation prompts, without tool names or job orchestration in those
prompts. No-charge discovery and request previews are optional, clearly
separate from billable generation. Exact JSON stays in the generated tool
reference; job/result contracts stay in the technical integration guide.
Changing prose does not constitute end-to-end installation testing in every
client edition or a live provider generation test.

The optional [agent skill guide](setup/agent-skill.md) distinguishes installing
workflow instructions from connecting the server. Prefer the Skills CLI with
`--skill ark-mcp` and an explicit scope; do not recommend all repository skills
or silently replace existing ones. Manual copying is a fallback. Both methods
install the complete `.agents/skills/ark-mcp/` bundle, including references and
agent metadata. A skills.sh listing depends on directory indexing and is not
proof of a tested install; do not promise immediate listing or generate fake
installation activity.
Keep links between bundled files relative and links to the handbook absolute,
so standalone copies do not depend on an Ark source checkout.
`skill-install.test.mjs` tests a relocated copy for identical contents and
resolvable local references, as well as the guide's paths and setup boundary.
Documentation CI also runs for changes to this skill.

Audio, Understanding, 3D and Avatars have separate navigation groups and
capability sections. Navigation tests ensure category membership and stable
tool routes; uncategorized or duplicate catalog entries fail the build.
The BytePlus logo uses the original light/dark SVGs from the reference site
in the header, with the same mark above the landing-page title. Its header
alternative text is empty because the adjacent site title already names
BytePlus; the hero mark is decorative.

`rehype-tables.mjs` wraps tables in focusable scroll regions, keeping native
table semantics and full-width columns without overflowing narrow screens.
Theme styles preserve visible keyboard focus and respect reduced motion.

`prepare-docs.mjs` calls `scripts/export_tool_docs.py` in an isolated Python
process. The exporter bypasses the package's eager server entrypoint, clears
inherited provider configuration, exports every catalog entry, and never
evaluates registration gates or calls handlers/providers. It writes JSON to
stdout, not credential files. Tests guard network, settings construction and
credential reads.

Relative Markdown handbook links become project-base-prefixed site routes.
References outside `docs/` point to GitHub source; reference definitions are
handled too. Build-time link validation catches missing pages and fragments.

Mermaid fences render locally in the browser through `astro-mermaid` and the
bundled `mermaid` library. Keep the Mermaid integration before Starlight; it
preserves the existing unified processor's link rewriting and table wrappers.
Automatic light/dark themes and strict security are enabled; no external
diagram service or CDN is used. Without JavaScript, source remains readable.
The renderer is loaded on pages containing diagrams, rather than on every
tool page. Mermaid's renderer produces a large lazy-loaded bundle; the build
can report a non-failing chunk-size warning.
Keep diagram source in the handbook, and verify SVG rendering in a production
preview after changes. `mermaid.test.mjs` checks real fence transformation,
HTML escaping, retained Markdown plugins and runtime script injection.

Update tool examples in `docs/site/tool-examples.json` when tool contracts
change. Examples use synthetic URLs/IDs, not real customer media, signed URLs,
credentials or private assets. Discovery is not a provider-entitlement test.
Never enable optional services or perform paid generation during a site build.

Model-specific guidance belongs in one **Supported Models** table on each
relevant generated tool page, not in separate model sections or product guides.
The table lists model/service IDs, defaults or configuration gates, and compact
capabilities. Do not invent built-in IDs for custom-bound families or imply that
all rows are enabled on every deployment. Fixed-service tools have no per-call
`model` selector. Utility and polling tools do not get unrelated model tables.
`tool-models.test.mjs` guards layout and scope; Python export tests compare the
documented built-in defaults with static settings fields without constructing
settings or calling providers. Keep summaries aligned with the capability registry.

Seedream's shared editing/output guidance remains in `seedream_generate_image`
under **Tool Reference > Images and video**.
Source notes under `docs/site/tool-notes/` are not standalone site pages.
The former `/seedream-5-5/` URL is an Astro static redirect; the old section
anchors now resolve above the supported-model table.

## GitHub Pages

`.github/workflows/docs.yml` tests and builds on relevant pull requests and
pushes. Only a successful `main` push or manual `main` run deploys.
Pull requests build without Pages write permissions.

To publish, select **Settings > Pages > Build and deployment > Source >
GitHub Actions** in the repository, then merge the reviewed changes to `main`.
The intended URL is `https://byteplus-sa.github.io/ark-mcp/`.
Creating this site locally does not publish it or change repository settings.
The workflow does not need provider credentials.

## Reference and License

Framework, layout and theme patterns are adapted from the local
`ComfyUI-BytePlus-ModelArk/docs-site` checkout and its
[published site](https://byteplus-sa.github.io/ComfyUI-BytePlus-ModelArk/).
The original MIT copyright and license notice is retained in
`docs/site/LICENSE`. ComfyUI nodes, workflows, screenshots and generated media
are not copied into the Ark MCP tool reference.

Documentation consulted 2026-11-10:

- [Starlight configuration](https://starlight.astro.build/reference/configuration/)
- [Starlight content collections](https://starlight.astro.build/guides/content-collections/)
- [Astro GitHub Pages deployment](https://docs.astro.build/en/guides/deploy/github/)
- [FastMCP tools](https://gofastmcp.com/servers/tools)

Mermaid integration consulted 2026-11-12:

- [astro-mermaid setup and Astro/Starlight compatibility](https://github.com/joesaby/astro-mermaid#readme)
