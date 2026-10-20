# Versioning and releases

Ark MCP uses semantic versions (`MAJOR.MINOR.PATCH`) and Git tags (`vX.Y.Z`).
The first managed release is `v0.2.0`. Releases are approved through pull
requests, not published for every commit to `main`.

## Release process

1. Merge reviewed changes to `main` using Conventional Commit messages.
2. Release Please opens or updates a release PR containing the version bump
   and [changelog](changelog.md). Its Python strategy updates `pyproject.toml`
   and `src/ark_mcp/__init__.py`; a targeted updater changes only the root
   package's version in `uv.lock`. The release manifest tracks the same version.
3. Review the changelog and compatibility implications, and wait for both CI
   and CodeQL on the release PR's exact head. Do not manually merge a failing
   release PR.
4. Merge the release PR. Release Please creates a version tag and a **draft**
   GitHub Release, then dispatches **Publish Release**.
5. Publication resolves the tag to a commit on `main`, reruns the full
   quality/security/package, CodeQL, and container-health CI at that commit, checks
   version consistency, and builds the distributions.
6. The publisher attaches the wheel, source distribution, and `SHA256SUMS`,
   pushes the versioned Docker image, records its digest in the release notes,
   and only then publishes the GitHub Release.

The initial empty `.release-please-manifest.json` is a one-time bootstrap.
Release Please fills it with `0.2.0` in the first release PR. Published tags
require all four version locations to agree; never reset the manifest to
bootstrap another release.

## Choosing versions

| Commit | Version change |
|---|---|
| `fix: ...` | Patch |
| `feat: ...` | Minor |
| `feat!: ...` or a `BREAKING CHANGE:` footer | Minor before 1.0; major from 1.0 onward |
| `docs: ...`, `ci: ...`, `chore: ...` | Normally no independent release |

Use `deps:` for dependency updates that need release notes. Squash PRs with
a conventional title, or preserve conventional commits when using merge
commits. Inspect the generated version rather than relying on the PR title
alone. To deliberately graduate to 1.0, follow Release Please's `Release-As`
override guidance and review the compatibility contract.

Only stable numeric versions are currently publishable. Alpha/beta/RC
channels and PyPI publication are not configured. Versions below 1.0 remain
pre-stable even though they are ordinary GitHub Releases.

## Installing a release

Download the wheel and `SHA256SUMS` from the
[GitHub Releases page](https://github.com/byteplus-sa/ark-mcp/releases).
Check the downloaded file's SHA-256 against the checksum entry, then install
that wheel with `uv pip install` in a virtual environment. Do not assume that
the `ark-mcp` name on PyPI belongs to this project.

Container tags:

- `ghcr.io/byteplus-sa/ark-mcp:v0.2.0` and `:0.2.0`: a particular release.
- `:latest`: the newest successfully published stable numeric version.
- `:edge`: a passing `main` build, not a release.
- `:sha-<full-commit-sha>`: a development image for a particular main commit.

Use an image **digest** from the release notes for an immutable deployment
pin. Containers currently target the Linux AMD64 runner platform; a native
ARM64 image is not published by this workflow. Configuration, credentials,
state volumes, and database backups remain operator responsibilities.

The source-checkout MCP launch (`uv --project ... run --frozen python -m
ark_mcp`) continues to use that checkout. Publishing a release does not
upgrade or restart TRAE or other MCP clients automatically.

## Repository setup

Enable **Settings > Actions > General > Allow GitHub Actions to create and
approve pull requests**. Keep the default workflow token permissions read-only;
the release job requests the specific write permissions it needs.

No PAT, extra registry password, or PyPI secret is needed. Release Please uses
`GITHUB_TOKEN`. Since bot-created PRs and tags do not trigger ordinary event
workflows, it explicitly dispatches CI, CodeQL, and publication. A release
PR must therefore have completed dispatched checks before merging.

The workflow does not approve or merge its own release PRs. Permissions to
create PRs do not replace human release approval.

## Recovery

If publication fails, the GitHub Release remains a draft. Fix the underlying
environment or workflow issue, then dispatch **Publish Release** on `main`
with the same existing tag:

```bash
gh workflow run publish-release.yml --ref main -f tag=v0.2.0
```

Existing assets are downloaded and compared before reuse. Differing bytes
stop publication instead of overwriting them; investigate build provenance
before any manual recovery. The container registry and GitHub are separate
systems: a failed final API call can leave a pushed image beside a draft
release. Publication is serialized, and retrying an older draft does not
move `latest` backwards.

Already-published releases are not republished by this workflow. Do not move
their tags or replace their assets; publish a new patch instead.

## Validation

```bash
uv run python scripts/release_checks.py
uv run pytest tests/unit/test_release_checks.py tests/unit/test_release_workflows.py
```

Publication additionally checks tag equality and the version/name inside
both distribution metadata files. CI still enforces the frozen lockfile,
offline tests and coverage, lint, typing, dependency/security checks, package
installation, and container health. No paid provider calls are part of a release.

## Sources

Accessed 2026-10-20:

- [Release Please Action and GITHUB_TOKEN event limitations](https://github.com/googleapis/release-please-action)
- [Manifest configuration and initial versions](https://github.com/googleapis/release-please/blob/main/docs/manifest-releaser.md)
- [Customizing releases and TOML updaters](https://github.com/googleapis/release-please/blob/main/docs/customizing.md)
