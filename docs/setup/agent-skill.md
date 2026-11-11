---
title: Install the Agent Skill
description: Install the Ark MCP workflow guide with the Skills CLI for Codex, Claude Code and other supported agents.
---

The **ark-mcp skill** teaches your agent how to use the connected server:
discover models, submit work, check progress, keep results and avoid duplicate
paid requests. It is optional; the MCP tools already describe their inputs
and outputs.

**The server provides tools. The skill provides instructions.** Installing
the skill does not install the server, connect a client, add credentials or
enable unavailable products. Complete [server setup](install.md) separately.
Ark MCP does not have a tool that installs agent skills.

## Ask Your Agent to Install It

Use the [Skills CLI](https://github.com/vercel-labs/skills), the installer for
the [skills.sh directory](https://skills.sh/). It downloads the skill from
GitHub and handles the selected agents' skill folders.

In a coding agent with terminal and file access, replace `[my agent]` and send:

```text
Install the official ark-mcp usage skill for [my agent], user-wide,
using the Skills CLI:
npx skills add byteplus-sa/ark-mcp --skill ark-mcp -g

Check prerequisites and ask before installing missing software.
Target only my agent; preserve installation confirmations.
If ark-mcp already exists, stop and ask before updating or replacing it.
Do not read or change credentials, MCP settings, or the server installation.
Verify the installed files match the source and the agent can discover
the skill. Report its path, source and any remaining approval or reload step.
Do not generate media or run paid tests.
```

The CLI requires **Node.js 22.20 or newer**, npm/npx and Git for this GitHub
source. These are skill-installer prerequisites, not additional MCP server
requirements. Your agent can check them and prepare the install; complete any
remaining interactive selection or approval yourself. It must report blocked
or unfinished steps, not claim success.

## Install Directly

If you prefer a terminal, run:

```bash
npx skills add byteplus-sa/ark-mcp --skill ark-mcp -g
```

Select only the agents you use and review the installation summary.
`--skill ark-mcp` selects the usage skill, not the other contributor skills
in this repository. `-g` installs user-wide. For one project, omit `-g` and
run from that project's directory. To preselect a client, add
`--agent codex` or `--agent claude-code`.

Review the skill before using it: its instructions run with the agent's
permissions. Avoid `--all` and automatic confirmation when an existing skill
might be replaced. The CLI installs the complete bundle, including
`references/` and `agents/`; no manual folder copying is normally needed.

## Where the Skill Goes

These are skill **folders**; each contains `SKILL.md` and its supporting files.
They are not MCP configuration files. The CLI can link client-specific paths
to a canonical `.agents/skills` copy; `--copy` selects independent copies.

| Agent | User-wide | Project-only |
| --- | --- | --- |
| Codex | `~/.agents/skills/ark-mcp/` | `<project>/.agents/skills/ark-mcp/` |
| Claude Code | `~/.claude/skills/ark-mcp/` | `<project>/.claude/skills/ark-mcp/` |

`~` means the user's home directory on the machine where the agent runs.
For a remote or cloud session, local laptop paths do not install anything
on that session's host. Ask the agent to resolve its actual environment.

Other clients, including TRAE, may use different skill roots or an import UI.
Check the installed edition's skill support; do not assume every MCP client
scans `.agents` or `.claude`. The Claude Code paths above do not establish
skill installation for Claude Desktop or claude.ai.

<details>
<summary>Share one project skill between Codex and Claude Code</summary>

Select both agents in the CLI and use its symlink installation mode.
The canonical folder is `.agents/skills/ark-mcp/`; `.claude/skills/ark-mcp`
can point to `../../.agents/skills/ark-mcp`. This is also the layout used by
the Ark repository. Edit the canonical folder, not a second copy.

Ask before replacing any existing folder or link. Verify the target resolves
and both agents discover it. Where symlinks are unavailable or disallowed,
use each client's supported installation method and keep any separate copies
at the same reviewed revision.

</details>

## Verify and Use It

An installed folder and a loaded skill are separate checks. Have the agent
confirm `SKILL.md`, `references/` and `agents/` are present, then check its
skill list. In Codex, use `/skills`; in Claude Code, look for `/ark-mcp` in
the command menu. Reload the skill catalog or open a new session if needed,
after active MCP jobs finish.

Then ask:

```text
Load the ark-mcp skill and summarize how you will discover available
models, submit work, check existing requests and keep completed results.
Do not generate media, upload files or make paid calls.
```

This checks instruction loading, not the MCP connection or provider access.
Use [discovery](first-run.md#discover-before-spending) to check the connection.
When ready to create, ask in normal language:

```text
Use the ark-mcp skill and my connected ark-seed server to generate one
image of a red ceramic cup on a wooden table in warm morning light.
Show me the finished image and a download link.
```

Generation may incur charges. Review the proposed operation before approving.
The connected server's discovery results and tool schemas are authoritative;
the skill does not make every documented product available.

## Updates

The repository shorthand follows its default branch, not an immutable release.
Keep the CLI's source/lock metadata. Ask your agent to check and update only
`ark-mcp` in the scope where it was installed:

| Installation | Check | Update after approval |
| --- | --- | --- |
| User-wide | `npx skills check ark-mcp -g` | `npx skills update ark-mcp -g` |
| Project-only | `npx skills check ark-mcp -p` | `npx skills update ark-mcp -p` |

Run project-only commands from the intended project directory. Keep the scope
explicit: a bare check can select project skills even when you intend to
update a user-wide copy. Review changes and keep the current copy until its
replacement is verified. Never overwrite local customizations without approval.
Updating a skill must not silently upgrade the server or change its credentials.
For older or customized servers, check compatibility against their live schemas.

## How skills.sh Listing Works

GitHub remains the source of truth; there is no separate skill upload or
publish command. According to the [skills.sh FAQ](https://skills.sh/docs/faq),
public skills enter the directory through normal CLI installation telemetry.
A successful install does not guarantee that a directory page is immediately
visible, and directory visibility is not required to use the install command.

The CLI enables anonymous install telemetry by default. You can opt out by
setting `DISABLE_TELEMETRY=1` or `DO_NOT_TRACK=1`; according to its
[telemetry documentation](https://github.com/vercel-labs/skills/blob/main/docs/troubleshooting.md#telemetry),
these settings also disable security-audit requests. Respect the user's
preference: telemetry is not required to install or use the skill.

<details>
<summary>Manual folder installation (fallback)</summary>

If the CLI is unavailable or unsupported for your agent, use the
[official skill folder](https://github.com/byteplus-sa/ark-mcp/tree/main/.agents/skills/ark-mcp).
Have your agent review and copy the entire folder from one source revision,
including `SKILL.md`, `references/` and `agents/`, to the supported destination
above. Do not copy a GitHub HTML page or the entire repository into a skills
directory.

Preserve existing files and ask before any replacement. Check that the installed
files match the source, record the revision and confirm skill discovery.
Installing a skill does not authorize executing downloaded scripts or reading
credentials.

</details>

## Sources

Checked 2026-11-11:

- [Official ark-mcp skill](https://github.com/byteplus-sa/ark-mcp/tree/main/.agents/skills/ark-mcp)
- [Skills CLI commands](https://github.com/vercel-labs/skills/blob/main/docs/commands.md)
- [skills.sh listing FAQ](https://skills.sh/docs/faq)
- [Codex skill-root discovery](https://github.com/openai/codex/blob/main/codex-rs/ext/skills/src/host_roots.rs)
- [Claude Code skills](https://code.claude.com/docs/en/skills)
