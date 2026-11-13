# Portable Execution

## Resolve the Environment

Treat OS, shell, client, and execution host as separate choices. A Windows
client may launch a server inside WSL; a macOS editor may use a Linux SSH
host. Resolve each explicitly before writing configuration. Never assume
Bash, PowerShell, a GUI, administrator privileges, or a particular CPU
architecture is available.

Use the current account's home directory or OS known-folder APIs, not an
author's username or a path copied from a previous machine. Honor existing
working installations and explicit path choices first.

Default paths for new installations:

| Environment | Managed installation root | Private configuration directory |
| --- | --- | --- |
| Linux | `$XDG_DATA_HOME/ark-mcp`, else `~/.local/share/ark-mcp` | `$XDG_CONFIG_HOME/ark-mcp`, else `~/.config/ark-mcp` |
| macOS | `~/.local/share/ark-mcp` | `~/.config/ark-mcp` |
| Native Windows | LocalApplicationData known folder + `ark-mcp` | LocalApplicationData known folder + `ark-mcp/config` |

Use `.env` inside the private configuration directory. On macOS, honor
explicitly configured absolute XDG paths too. Ignore empty or relative XDG
values rather than interpreting them against an arbitrary working directory.
On Windows, prefer known-folder resolution; `%LOCALAPPDATA%` is its usual
environment representation, not a literal path component. Stop and ask for
a writable private location if the selected root is unavailable.

Do not assume every home is on a local filesystem. Redirected Windows
profiles, network mounts, shared folders, and cloud sync may require another
location before credential entry.

## Commands and Paths

- Prefer process APIs taking an executable and argument array. Resolve `uv`
  or `uv.exe` on the server host; inspect the executable path only. Do not
  assume the GUI client inherits the agent terminal's PATH.
- Bash examples in this skill are for Bash-compatible shells. Do not paste
  their backslash continuations or `$VAR` assignments into PowerShell or
  cmd.exe. Use the PowerShell examples in `clients.md`, native argument
  arrays, or a verified shell-specific equivalent.
- Paths with spaces and non-ASCII usernames are valid. Preserve them exactly
  and quote each shell argument separately. Do not split a resolved path on
  whitespace, remove accents, or interpolate it into executable shell text.
- JSON requires escaping Windows backslashes; a JSON serializer is preferred.
  Forward-slash Windows paths are another option for these Python/uv paths.
  TOML literal strings can preserve backslashes without escape processing.
  Expand home and environment placeholders before serialization.
- Example Windows launcher paths below are placeholders, not defaults:

```json
{
  "command": "C:/path with spaces/uv.exe",
  "args": [
    "--directory", "C:/path with spaces/ark-mcp/releases/COMMIT",
    "run", "--locked", "--no-sync", "python", "-m", "ark_mcp"
  ],
  "env": {
    "ARK_MCP_ENV_FILE": "C:/private configuration/ark-mcp/.env",
    "MCP_TRANSPORT": "stdio",
    "MCP_AUTH_MODE": "local"
  }
}
```

For WSL/SSH/container launches, all existing examples assume the client and
server share an OS namespace. A cross-host launch needs the client's
documented remote launcher, correct path translation, and a separate
credential-entry decision. Never put a Linux path directly in a native
Windows executable field or silently copy a key across hosts.

## Skill Distribution and Verification

This skill is the `ark-mcp-setup` directory with its bundled `references/`.
All required instruction links resolve within that directory. Look up server
docs in the selected downloaded checkout, not relative to an assumed skill
installation path. The repository's `.claude/skills/` symlink is a development
mirror; a consumer on Windows can install the canonical directory without
creating symlinks or enabling Developer Mode.

Use POSIX permissions or Windows ACLs as described in `credentials.md`.
On headless machines, pause for the user's private interactive editing
session; do not install a GUI editor, print secrets, or claim completion.

Validate syntax and discovery on the actual selected host. A Bash syntax
check on macOS does not prove PowerShell works, and a local smoke test does
not prove every client/OS combination works. Record untested platforms
explicitly. Do not install additional agents or modify real credentials
solely to expand a portability test matrix.
