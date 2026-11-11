---
title: Install the Agent Skill
description: Give your agent the Ark MCP workflow guide, with installs for Codex, Claude Code and other skill-capable clients.
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

Use a coding agent with permission to download files and write to the target
agent's skill directory. Replace `[my agent]`, then send:

```text
Install the official ark-mcp usage skill for [my agent], user-wide:
https://github.com/byteplus-sa/ark-mcp/tree/main/.agents/skills/ark-mcp

Confirm the skill directory supported by my agent before writing.
Download and review the complete ark-mcp folder, including references/
and agents/, from one source revision. Preserve its files and structure.
If ark-mcp already exists, stop and ask before updating or replacing it.
Do not read or change credentials, MCP settings, or the server installation.
Verify the installed files match the source and the agent can discover
the skill. Report the installed path, source revision and any remaining
reload or approval step. Do not generate media or run paid tests.
```

The agent should install **the entire folder**, not just `SKILL.md`, a GitHub
HTML page, or the whole repository inside a skills directory. Review downloaded
instructions before enabling them; installing a skill does not authorize
executing downloaded scripts. If the agent cannot access the source or write
to the correct host, it should report that limitation rather than claim success.

For a project-only install, replace `user-wide` with
`in this project only: [absolute project path]`. A project-scoped skill is
useful when a team wants reviewed instructions kept with its project.

## Where the Skill Goes

These are skill **folders**; each contains `SKILL.md` and its supporting files.
They are not MCP configuration files.

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

Keep the canonical folder at `.agents/skills/ark-mcp/` and, where supported,
have your agent create `.claude/skills/ark-mcp` as a relative symlink to
`../../.agents/skills/ark-mcp`. This is the layout used by the Ark repository.
Edit the canonical folder, not a second copy.

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

The source link follows `main`, not an immutable release. Record the downloaded
commit, review changes before updating, and keep the current copy until its
replacement is verified. Never overwrite local customizations without approval.
Updating a skill must not silently upgrade the server or change its credentials.
For older or customized servers, check compatibility against their live schemas.

## Sources

Checked 2026-11-11:

- [Official ark-mcp skill](https://github.com/byteplus-sa/ark-mcp/tree/main/.agents/skills/ark-mcp)
- [Codex skill-root discovery](https://github.com/openai/codex/blob/main/codex-rs/ext/skills/src/host_roots.rs)
- [Claude Code skills](https://code.claude.com/docs/en/skills)
