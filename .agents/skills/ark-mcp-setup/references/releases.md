# Release Selection and Safe Updates

## Resolve Upstream

Default repository: `byteplus-sa/ark-mcp` on GitHub.
Use `gh` for GitHub queries where available:

```bash
gh api repos/byteplus-sa/ark-mcp/releases/latest \
  --jq '{tag_name, draft, prerelease, html_url}'
```

Require `draft=false` and `prerelease=false`. GitHub's latest published
stable release is the default, not the highest-looking tag or newest
default-branch commit. Honor an explicitly requested version.

If this returns 404, distinguish an absent release from lost access:

```bash
gh api repos/byteplus-sa/ark-mcp --jq '{full_name, default_branch, private}'
gh api --paginate repos/byteplus-sa/ark-mcp/releases \
  --jq '.[] | {tag_name, draft, prerelease}'
```

If repository access works and there are no published stable releases,
ask: "No stable release is published. Use the current default-branch
commit, choose a specific ref, or stop?" Fetch the default branch name
from repository metadata; do not assume `main`.

Authentication, rate limits, network failures, or inconsistent release
metadata require diagnosis, not automatic fallback. If `gh` is unavailable,
ask to install/use an approved GitHub integration. Never request a GitHub
token in chat. As of 2026-10-20, this repository had no published releases;
query again on every setup/update rather than treating that as permanent.

## Stage, Do Not Overwrite

Use a new, empty destination under the managed root's `releases/`.
Do not derive filesystem paths from an unvalidated remote tag. Prefer a
resolved hexadecimal commit SHA for the final directory name. Inspect the
parent directory before creation and refuse symlink/reparse-point surprises.

1. Clone `https://github.com/byteplus-sa/ark-mcp.git` with `--no-checkout`
   into the new destination. Do not modify an unrelated existing checkout.
2. Resolve the selected release using its exact `refs/tags/<tag>` ref,
   fetching that ref if needed. Validate the ref with `git check-ref-format`.
   Use argument arrays or quoted variables, never `eval` or remote strings
   concatenated into shell code.
3. Resolve `refs/tags/<tag>^{commit}` to a commit SHA, then switch the new
   checkout to that SHA using `git switch --detach`. For an authorized
   branch fallback, fetch that exact branch and pin its resolved commit.
4. Record the release URL, tag/ref, and commit SHA. A SHA identifies the
   downloaded code; it does not by itself prove a signed release.
5. Read `pyproject.toml`, `uv.lock`, `README.md`, `.env.example`, and applicable
   migration notes from the selected checkout. Check that it supports
   `ARK_MCP_ENV_FILE` before using this skill's launcher.
6. Run `uv sync --locked --no-dev` in the staged directory. Failure must not
   change the active MCP entry. Do not fix a lock mismatch with `uv lock`.
7. Validate the staged server before switching the client's checkout path.
   Record enough non-secret metadata to identify the previous installation.

For reuse, verify origin, HEAD, working-tree cleanliness, and locked
dependencies. An identical requested SHA with successful health checks is a
no-op. Do not automatically run `git pull`, reset, stash, or clean a user's
working tree. If a tag now resolves to a different SHA from a prior install,
surface that discrepancy and ask before replacing it.

## Updates and Rollback

- Preserve the private env file unchanged. Review new variable requirements
  and retired settings; ask the user to make needed credential changes in
  their local editor. Do not merge `.env.example` over populated credentials.
- Check migrations before startup. Staging with production SQLite/artifact
  paths can mutate state, so use isolated temporary state/artifact directories
  for preflight. Do not delete or migrate the active database as a side effect.
  If a release has irreversible state migrations, explain and obtain approval
  for a backup/migration before activation; code rollback alone is insufficient.
- Check active jobs before restart. Keep the old checkout until activation
  and target-client verification succeed.
- Back up client configuration with private permissions and change only
  `ark-seed`. Recheck the file for concurrent edits before applying changes.
- If activation fails, restore only the previous entry if it has not changed
  concurrently. Keep credentials, artifacts, and database state intact.
- Do not automatically delete old releases, backups, or user data.
  Explain what remains and ask before cleanup.

## References

- [GitHub latest release API](https://docs.github.com/en/rest/releases/releases#get-the-latest-release)
- Repository-specific runtime/migration contracts live in the chosen
  checkout's `docs/configuration.md`, `docs/getting-started.md`, and
  `docs/migration-0.2.md` where applicable.
