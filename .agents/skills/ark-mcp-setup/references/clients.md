# Client Selection and Registration

Use runtime metadata plus the user's preference, not directory existence
alone. Detect the OS and execution host before selecting paths. This file
is a starting contract; check installed CLI help and current official docs
when a version differs. Do not print existing configuration containing keys.

## Claude Code

Prefer the CLI. Verify `claude mcp add --help`. For global registration:

```bash
claude mcp add --transport stdio --scope user \
  --env "ARK_MCP_ENV_FILE=$ENV_FILE" \
  --env MCP_TRANSPORT=stdio --env MCP_AUTH_MODE=local \
  ark-seed -- "$UV" --directory "$CHECKOUT" \
  run --locked --no-sync python -m ark_mcp
```

The variables above contain only resolved paths, never credentials.
For PowerShell, resolve `$EnvFile`, `$Uv`, and `$Checkout` as absolute paths
on the server host first, then use this equivalent without Bash continuations:

```powershell
claude mcp add --transport stdio --scope user --env "ARK_MCP_ENV_FILE=$EnvFile" --env MCP_TRANSPORT=stdio --env MCP_AUTH_MODE=local ark-seed -- $Uv --directory $Checkout run --locked --no-sync python -m ark_mcp
```

`user` is global; Claude's default `local` scope is private to the current
project and is **not** global. For a private project installation use
`--scope local`; for explicitly shareable project configuration use
`--scope project`, which writes `.mcp.json`. Resolve this distinction when
the user asks for "local." Run the command with the target project as cwd.

User/local metadata is normally in `~/.claude.json`; a configured
`CLAUDE_CONFIG_DIR` changes the location. Prefer the CLI instead of guessing.
Do not blindly re-add an existing entry or remove it without a protected
backup. Check `/mcp` from Claude Code after reload. CLI `list`/`get` output
can contain configuration values; capture and sanitize it before displaying.

## Codex

Global configuration is `${CODEX_HOME}/config.toml`, defaulting to
`~/.codex/config.toml`. Verify `codex mcp add --help`, then for global:

```bash
codex mcp add ark-seed \
  --env "ARK_MCP_ENV_FILE=$ENV_FILE" \
  --env MCP_TRANSPORT=stdio --env MCP_AUTH_MODE=local \
  -- "$UV" --directory "$CHECKOUT" \
  run --locked --no-sync python -m ark_mcp
```

PowerShell equivalent, with the same resolved path variables:

```powershell
codex mcp add ark-seed --env "ARK_MCP_ENV_FILE=$EnvFile" --env MCP_TRANSPORT=stdio --env MCP_AUTH_MODE=local -- $Uv --directory $Checkout run --locked --no-sync python -m ark_mcp
```

`codex mcp add` writes globally; do not use it for a project-only request.
For project scope, merge this entry into the trusted project's
`.codex/config.toml`:

```toml
[mcp_servers.ark-seed]
command = "/absolute/path/to/uv"
args = ["--directory", "/absolute/path/to/checkout", "run", "--locked", "--no-sync", "python", "-m", "ark_mcp"]

[mcp_servers.ark-seed.env]
ARK_MCP_ENV_FILE = "/absolute/path/to/private/.env"
MCP_TRANSPORT = "stdio"
MCP_AUTH_MODE = "local"
```

Check project trust and any existing global entry that affects resolution.
Do not change project trust automatically. Use `/mcp` or the target client's
tool discovery after reload. Codex CLI, IDE extension, and app may share
configuration; do not register three copies.

## TRAE IDE and TRAE Work

Identify International versus CN and IDE versus Work from the active
runtime. Do not guess a global path such as `~/.trae/mcp.json` merely because
the directory exists.

- **Global default:** use the selected edition's MCP settings UI to add or
  edit a user-level server. In TRAE IDE this is Settings > MCP > Add >
  Manual configuration. In Work use Settings > MCP > Create > Manual
  configuration. Labels can vary; verify the UI and selected scope.
- **Project:** the repository's documented IDE integration uses
  `<project>/.trae/mcp.json`, with project-level MCP enabled in settings.
  Check support in the installed edition before writing it.
- Use the `mcpServers` JSON envelope around the launch contract from
  `SKILL.md`. Keep only the credential-file path in `env`.
- Work may select local or cloud runtime. Default this workflow to local;
  a cloud selection needs its own accessible checkout and credentials.
- If tools cannot operate the UI or establish a documented global config
  location, produce the non-secret JSON and guide the user through the UI.
  Mark registration pending until confirmed. Never substitute a project
  config while claiming a global installation.

## Claude Desktop

Do not confuse Desktop with Claude Code. Merge the `mcpServers` envelope
into the native Desktop config:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

Use Desktop's developer settings to resolve the actual file when uncertain.
This is user-level configuration. Do not invent a project scope; ask whether
global is acceptable or the user meant Claude Code. Confirm support before
attempting other platforms. Fully quit/reopen only after checking active work.

## Other Clients

Ask which client when detection is uncertain. Use its current official
MCP documentation rather than reusing another product's schema.

- Cursor: `mcpServers` in `~/.cursor/mcp.json` globally or
  `<project>/.cursor/mcp.json` for project scope.
- VS Code: workspace `.vscode/mcp.json` uses `servers`, not `mcpServers`.
  Use the native MCP add/configure command for the selected user profile's
  global scope; do not guess its platform-specific storage path.
- OpenCode: `mcp.ark-seed` uses `type: "local"`, `command` as an array and
  `environment` for environment variables. Use the selected profile's
  documented global or project config; preserve JSONC comments.

Only implement the selected client. If its launcher cannot supply
`ARK_MCP_ENV_FILE`, stop and find a supported private environment-file
mechanism instead of inlining keys.

## Sources

Checked 2026-10-20; recheck for the installed client version when needed.

- Claude Code scopes and commands:
  <https://code.claude.com/docs/en/mcp-quickstart>
- Codex MCP:
  <https://developers.openai.com/codex/mcp/>
- Codex CLI global-only add implementation, checked through Context7:
  <https://github.com/openai/codex/blob/main/codex-rs/cli/src/mcp_cmd.rs>
- TRAE Work runtime selection, linked by this repository's integration guide:
  <https://docs.trae.cn/work/remote-mcp-server>
- This repository's `README.md` and `docs/integration-guide.md` document
  Desktop, TRAE, Cursor, VS Code, and OpenCode connection templates.
  Read those files from the selected checkout; they are not assumed to be
  adjacent to an independently installed copy of this skill.
