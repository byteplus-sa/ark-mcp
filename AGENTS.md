# AGENTS.md

## Project

Ark Seed Multimodal MCP Server — a Python MCP server (FastMCP on uv)
that exposes BytePlus multimodal generation through a small, typed, safe tool
surface:

- **Seed Audio** — full-scene audio generation through Seed Speech.
- **Seedream** — image generation and editing through ModelArk.
- **Seedance** — asynchronous video generation and task management through
  ModelArk. Supports two model generations: 2.0 (legacy, 15s, 4K) and 2.5
  (default, 30s, 1080p, structured editing, native extension). Seedance 2.5
  Premium (`dreamina-seedance-2-5-premium-260915`, adds 4K) is a separate,
  whitelist-only family with its own `seedance_2_5_premium_*` tools, gated by
  `BYTEPLUS_MODELARK_SEEDANCE_2_5_PREMIUM_ENABLED`; disabled by default.
- **Seed 3D** — asynchronous 3D model generation through ModelArk (Hyper3D
  text-to-3D and Hitem3d image-to-3D). Gated by `BYTEPLUS_MODELARK_3D_ENABLED`;
  disabled by default.
- **Seed 2.1 Understanding** — multimodal video/image understanding and
  reasoning through ModelArk Chat Completions; supports deep-thinking mode.
- **Seed Audio Understanding** — audio understanding and reasoning
  (transcription, translation, analysis, Q&A) through ModelArk Chat Completions
  `input_audio`; model ID set by `SEED_AUDIO_UNDERSTANDING_MODEL`.
- **Speech-to-Text** — background audio transcription through Seed Speech ASR.
- **VOD AI MediaKit** — asynchronous video enhancement, video transcoding,
  subtitle burn-in and precision subtitle/text erasure, and voice/background
  audio separation through the BytePlus VOD AI MediaKit.
- **Object storage upload** — presigned URL generation for URL-only media
  workflows (TOS or S3 backend).
- **Private asset library** — `ark_asset_*` tools for Dreamina Seedance
  Advanced Creation Rights assets (AIGC virtual portraits, real-person
  liveness verification, uploads) through the AK/SK-signed ModelArk OpenAPI;
  registered only when `BYTEPLUS_MODELARK_ACCESS_KEY` and
  `BYTEPLUS_MODELARK_SECRET_KEY` are set. Seedance accepts `asset://<id>`
  references natively.
- **Background jobs** — ordinary `ark_job_*` tools are the default route for
  long-running work through the FastMCP worker, since most clients cannot
  negotiate MCP task augmentation; native tasks remain supported for clients
  that can.
- **Durable artifacts** — MCP resources for generated media whose provider URLs
  expire (2h for audio, 24h for image/video/3D).
- **Transports** — local `stdio` first, with protected Streamable HTTP as a
  deployable option (both natively supported by FastMCP).

Seedance, Seedream, Seed 3D, Seed 2.1 Understanding, and Seed Audio
Understanding share the ModelArk data-plane host and Bearer authentication.
Seed Audio and Speech-to-Text are
hosted by Seed Speech and use `X-Api-Key`. VOD AI MediaKit uses a separate
Bearer key. The asset library uses BytePlus IAM AK/SK with OpenAPI V4
signatures. The server uses three provider gateways behind one normalized
domain layer. See `plans/PLAN_ARK_SEED_MULTIMODAL_MCP.md` for the full
design.

## Repository Layout

```text
ark-mcp/
├── AGENTS.md          # project conventions for agents (this file)
├── CLAUDE.md          # redirects to AGENTS.md
├── README.md          # project overview and quickstart
├── Makefile           # task runner (wraps uv + fastmcp CLI)
├── pyproject.toml     # project metadata and dependencies (uv)
├── uv.lock            # locked dependencies for reproducible installs
├── fastmcp.json       # declarative FastMCP server configuration
├── .agents/skills/    # canonical project skills (source of truth)
├── .claude/skills/    # per-skill symlinks mirroring .agents/skills/
├── plans/             # implementation plans for features
├── specs/             # future-looking specs and design docs
├── docs/              # project documentation
├── scripts/           # live smoke tests and utility scripts
├── src/ark_mcp/  # server source (Python package)
└── tests/             # tests (pytest)
```

## Skills and Agent Configuration

Agent skills and the agent rule files must stay aligned across the tools
that read this repository:

- **`.agents/skills/` is the canonical source of truth** for project
  skills. Add and edit skills here. `skills-lock.json` pins externally
  sourced skills (e.g. `mcp-builder` from `anthropics/skills`); locally
  authored skills (`fastmcp`, `fastmcp-docs`) are committed directly.
- **`.claude/skills/` mirrors `.agents/skills/`** through one relative
  symlink per skill (e.g. `.claude/skills/mcp-builder` →
  `../../.agents/skills/mcp-builder`). When you add a skill under
  `.agents/skills/<name>/`, also create the matching
  `.claude/skills/<name>` symlink so Claude Code discovers the same skill.
  Never edit files through the `.claude/skills/` symlinks — change
  `.agents/skills/` and both trees update.
- **Keep project skills current with the MCP server.** When a code change
  adds, removes, renames, or materially changes MCP tools, resources,
  configuration, auth scopes, transport behavior, or output schemas, update
  the affected skill docs in `.agents/skills/` in the same unit of work.
  Treat agent-facing skills as shipped documentation for the current server,
  not as optional follow-up polish.
- **`CLAUDE.md` imports `AGENTS.md`.** `CLAUDE.md` contains a single
  `@AGENTS.md` import so Claude Code loads the same rules. Edit `AGENTS.md`
  only; never duplicate content into `CLAUDE.md`, so the two stay in sync.

## Documentation Standard

All non-code documentation lives in one of three top-level directories. Keep
their purposes distinct and do not mix them.

### `plans/` — Plans for features

Concrete, actionable implementation plans for a specific feature or unit of
work. A plan describes **how** to build something, with enough detail (code
structures, API signatures, data models, file organization, phases) that an
engineer or agent can execute it without re-deriving the design.

- Naming convention: `PLAN_<FEATURE_OR_SCOPE>.md`.
- Include frontmatter (`title`, `type: plan`, `status`, `created`, `updated`,
  `tags`, `source`, `related`) when the plan draws on external research.
- A plan is a living document: update `status` and `updated` as work
  progresses. Once shipped, the durable decisions should move to `specs/` or
  `docs/` and the plan can be archived or marked `status: shipped`.
- Reference the plan by path when writing code that implements it.

### `specs/` — Future-looking specs and design docs

Specifications describing what the system **is or will be**, independent of any
single implementation effort. Specs are the source of truth for contracts,
interfaces, and intended behavior. A spec may describe current reality ("what
it is today") or a future state ("what it will become"); label the horizon
explicitly when it matters.

- Naming convention: `SPEC_<DOMAIN_OR_CAPABILITY>.md`, or a short descriptive
  filename for cross-cutting specs (e.g. `security.md`, `error-model.md`).
- Include frontmatter with `status: draft | proposed | accepted | deprecated`
  and a `horizon: current | future` field when the spec is forward-looking.
- Specs hold: API contracts, tool schemas, data models, security models, error
  taxonomies, capability registries, and architecture decisions that outlive a
  single plan.
- Prefer updating an existing spec over creating a new one. When a spec is
  superseded, mark it `deprecated` and point to the successor rather than
  deleting it.

### `docs/` — Project documentation

User- and contributor-facing documentation for the project as it exists today.
This is the published handbook: how to install, configure, run, deploy, and
troubleshoot the server.

- Examples: `getting-started.md`, `configuration.md`, `transports.md`,
  `tools.md`, `deployment.md`, `troubleshooting.md`.
- Documentation describes **current** behavior. When something is planned but
  not yet built, link to the relevant entry in `plans/` or `specs/` instead of
  documenting it as if it already works.
- Keep docs in sync with the shipped code. If the code and docs disagree, the
  code is correct until the docs are updated; open a follow-up if they cannot
  be reconciled immediately.

## Working Conventions

- **Read before writing.** Before implementing, read the relevant plan in
  `plans/`, the applicable specs in `specs/`, and any existing docs in `docs/`.
- **Plans reference specs.** A plan may quote or link to a spec, but the spec
  remains the long-lived source of truth. Do not duplicate a contract into a
  plan and then let the two drift; link instead.
- **Docs reflect shipped work.** Do not document a feature in `docs/` until it
  is built. Use `plans/` and `specs/` for not-yet-shipped work.
- **Keep docs and skills in lockstep with shipped MCP changes.** If a server
  change affects tool inventory, resources, schemas, env vars, or user-facing
  workflows, update the relevant files in `docs/`, `README.md`, and
  `.agents/skills/` before considering the work complete. If full reconciliation
  is too large for the current task, document the remaining drift explicitly.
- **Frontmatter.** Plans and specs use YAML frontmatter. Docs may use
  frontmatter for title and weight/order but are not required to.
- **Diagrams.** Use Mermaid for architecture, sequence, and flow diagrams. Keep
  diagrams in the relevant plan or spec and update them when the design
  changes.
- **Sources.** When a plan or spec draws on external documentation, record the
  URLs in a `source:` frontmatter field or a `## Sources` section with access
  dates. Prefer official BytePlus and Model Context Protocol sources.
- **Secrets.** Never commit credentials, API keys, or provider response
  fixtures containing real media. Redact before saving examples.

## Repository Hygiene

The repository should contain only source code, tests, configuration,
documentation, plans, specs, and skills — **not** generated media, scratch
reports, or transient review artifacts. The following must never be committed:

- **Generated media at the repository root.** This includes `*.mp4`, `*.wav`,
  `*.mp3`, `*.jpeg`, `*.png`, `*.mov` files produced by the MCP tools, smoke
  tests, or manual experiments (e.g. `ppop-*.wav`, `output_*.mp4`,
  `seedance_*_test_*.mp4`). Move them to `.artifacts/` or `out/` (both
  gitignored) or delete them.
- **Generated output directories.** `output/`, `assets/`, and `sample/` are
  scratch containers for demo or experiment output. Do not commit them.
- **PR review scratch files.** `pr-reviews/` holds transient review reports
  produced during PR cycles. Delete them after the PR is merged or closed.
- **One-off reports and handovers.** Files like `CODE_REVIEW_REPORT.md` and
  `HANDOVER.md` are session-scoped artifacts. If their content has lasting
  value, distill it into `docs/` or `specs/`; otherwise delete them.
- **Shipped, superseded, or deprecated plans.** Once a plan reaches
  `status: shipped`, `status: superseded`, or `status: deprecated`, remove it
  from `plans/`. Move durable decisions into `specs/` or `docs/` first.
- **Build artifacts and runtime state.** `.artifacts/`, `out/`, and `dist/`
  are gitignored. Never force-commit them.

When in doubt about whether a file is scratch, treat it as scratch and delete
it. Practice CLAYGO — clean as you go, not just at the end of a task.

## Tool Contract Rules

Every MCP tool must be self-describing for MCP clients (Claude Desktop,
Cursor, TRAE, etc.) without requiring external documentation. These rules are
mandatory for all new and modified tools:

- **Function docstring required.** Every tool handler function MUST have a
  docstring. FastMCP uses this as the tool's `description` field in the MCP
  schema. A tool with no docstring appears as an empty description to clients.
- **Every field needs a description.** Every field on every Pydantic model
  that appears in a tool's input or output — including nested and shared
  domain models — MUST have a `Field(description=...)`. Bare declarations like
  `task_id: str` are not acceptable. This includes `Literal` fields with
  defaults (e.g. `provider: Literal["x"] = Field("x", description=...)`).
- **Descriptions must be accurate.** Field descriptions must match what the
  code actually produces. Reference units (seconds, milliseconds, bytes),
  enumerations, ranges, and mutual exclusivity where applicable.
- **Output models are client-facing.** MCP clients use JSON schema field
  descriptions to interpret tool results. Treat output model descriptions as
  shipped user documentation, not internal comments.
- **Shared domain models are client-facing.** Models in `domain/models.py`,
  `domain/media.py`, `domain/artifacts.py`, and `domain/transcription.py`
  appear in tool output schemas. All their fields need descriptions too.
- **Internal-only models are exempt.** Provider DTOs, config models, and
  security models that never appear in an MCP tool's `inputSchema` or
  `outputSchema` do not need descriptions (though they benefit from them).
