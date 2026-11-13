# Provider Keys with BytePlus Secret Manager

The deployment CLI reads an **exact BytePlus generic-secret version on the
operator workstation**, using scoped IAM/STS credentials. It delivers provider
keys through the existing encrypted guest transfer. The VM receives no KMS IAM
credentials and makes no startup KMS call. Secret Manager is needed for new
plans, not application startup or pending-plan resume.

This integration has offline API, encrypted-transfer, host-transaction and CLI
coverage. **Account enablement, IAM scoping and live sync have not been
validated.** No live secrets, IAM policies or encryption keys are created by
the CLI. Confirm account support, costs and permissions separately.

## Prerequisites

Complete the [deployment prerequisites](byteplus-terraform.md#requirements) and
[IAM setup](byteplus-terraform.md#set-up-iam-and-credentials). For Secret Manager,
review KMS `DescribeSecret` and `GetSecretValue` on the intended secret/project,
including encryption-key access where required. This is a capability checklist,
**not a validated least-privilege policy**. Do not broaden IAM automatically.

Only the officially documented Johor endpoint
`kms.ap-southeast-1.byteplusapi.com` is supported, independently of the VM region.
The existing operator `BYTEPLUS_ACCESS_KEY`, `BYTEPLUS_SECRET_KEY` and matching
optional `BYTEPLUS_SESSION_TOKEN` authenticate reads. Prefer temporary scoped
credentials. No extra SDK, ArkCLI or permanent VM IAM key is needed.

## Prepare the Secret

In BytePlus KMS **Secret Manager**, create a dedicated **Generic** secret in
the intended project. Enter provider keys through the private console/editor,
never chat, shell history, Terraform or committed files. Store a JSON object:

| Service | Bundle field | Used for |
|---|---|---|
| ModelArk | `BYTEPLUS_MODELARK_API_KEY` | Image/video generation, multimodal understanding, and enabled 3D |
| Seed Speech | `BYTEPLUS_SEED_SPEECH_API_KEY` | Seed Audio and speech-to-text |
| VOD AI MediaKit | `BYTEPLUS_VOD_MEDIAKIT_API_KEY` | Video processing and audio separation |
| Vision AI | `BYTEPLUS_CV_API_KEY` | Enabled OmniHuman avatar generation |

Obtain dedicated deployment keys from the corresponding service, following
[Provider Keys](api-keys.md). Secret Manager stores these keys; it does not
issue them or enable products. Populate every service you intend to retain:
the empty entries below are examples of deliberately disabled services.

```json
{
  "BYTEPLUS_MODELARK_API_KEY": "ENTER-REAL-KEY-IN-PRIVATE-CONSOLE",
  "BYTEPLUS_SEED_SPEECH_API_KEY": "",
  "BYTEPLUS_VOD_MEDIAKIT_API_KEY": "",
  "BYTEPLUS_CV_API_KEY": ""
}
```

The bundle is the **complete desired set of provider keys**: omitted or empty
entries disable those products; at least one key must be nonempty. Only these
four names are allowed. Duplicate fields, non-string values, oversized values,
whitespace, non-ASCII and interpolation characters are rejected. Feature flags,
provider hosts, limits, model bindings, registry credentials and the shared MCP
key are outside this provider-only integration.

## Set Up Private Reference Files

Create a private operator directory, then create `secret-reference.json` there
through a trusted editor. This file contains metadata only, not provider keys:

```bash
install -d -m 0700 "$HOME/.config/ark-mcp-deploy"
```

Record the exact version in a **nonsecret**, same-owner regular mode-`0600`
reference file outside the checkout:

```json
{
  "region": "ap-southeast-1",
  "account_id": "REPLACE-WITH-ACCOUNT-ID",
  "project_name": "default",
  "secret_name": "ark-team-providers",
  "version_id": "REPLACE-WITH-EXACT-VERSION-UUID"
}
```

After creating the reference file, restrict its permissions:

```bash
chmod 0600 "$HOME/.config/ark-mcp-deploy/secret-reference.json"
```

The `/private/...` command paths below are placeholders. Replace them with
your actual reference and CLI workspace paths. Run commands from the reviewed
checkout root with the operator IAM credentials already loaded, as described
in [IAM setup](byteplus-terraform.md#set-up-iam-and-credentials).

Replace every placeholder. The CLI uses the documented conservative name limit
of 2-31 ASCII letters, digits, underscores or hyphens and a canonical lowercase
version UUID. Account, project, name, Generic type and unmanaged status must
match `DescribeSecret` before retrieval. The `GetSecretValue` response must
match the requested `VersionID`; **the CLI never implicitly follows `CURRENT`**.

## First Deployment

First follow the [guided deployment workflow](byteplus-terraform.md#guided-deployment-cli)
to create a new workspace with `init` and review the infrastructure `plan`.
Replace `--provider-env-file` in the approved deployment command with:

```bash
uv run --no-sync python scripts/deploy_byteplus.py deploy --workspace /private/ark-team \
  --secret-reference-file /private/secret-reference.json --approve-plan PLAN_DIGEST
```

These input flags are mutually exclusive. Independent team, Redis and job
encryption secrets are generated once and retained as before. Resume after local
configuration was saved reuses it without another secret-value read. Supplying
a different reference requires an approved configuration sync.

## Update Provider Keys

These commands require a **verified CLI-managed deployment initialized with
this implementation**:

```bash
uv run --no-sync python scripts/deploy_byteplus.py config status \
  --workspace /private/ark-team
uv run --no-sync python scripts/deploy_byteplus.py config plan \
  --workspace /private/ark-team --secret-reference-file /private/secret-reference.json
```

`status` reads local key presence and source metadata, never values or KMS.
`plan` reads the pinned version and privately stages the complete prior/candidate
configuration. Output includes changed key names, source metadata, an approval
digest and maintenance notice, never values or per-key hashes. Approval binds
frozen source, exact VM, reference/version, sequence and complete configuration
digests. It does not grant IAM access.

**Pause producers and drain active jobs before approval.** This is controlled
maintenance with bounded shutdown grace, not automatic queue draining or a
guarantee that paid work survives interruption. Then:

```bash
uv run --no-sync python scripts/deploy_byteplus.py config apply \
  --workspace /private/ark-team --approve-config CONFIG_PLAN_DIGEST
```

The host checks the existing configuration/receipt, refuses non-provider
changes, atomically installs the candidate, and recreates **only MCP**.
The MCP connection key, Redis password, job encryption key, Caddy, SQLite,
artifacts, TLS data and all non-provider configuration remain unchanged.
Startup failure restores the prior configuration and attempts MCP-only recovery.
If recovery fails, preserve private evidence and inspect application health.

HTTPS/authentication/discovery and a supported nonbillable dry run are checked
after sync. **These checks do not prove provider credentials work.** A real
provider test requires a separately approved budget where applicable.

## Resume or Discard

Accepted invocation IDs and staged bytes survive interruption. Resume polls
the same operations and does not reread a newer secret:

```bash
uv run --no-sync python scripts/deploy_byteplus.py resume --workspace /private/ark-team
```

Unknown acceptance blocks replay. Known failed repeatable guest commands can
use `--retry-failed` only after reconciliation, with a fresh encrypted
transport. The durable host transaction identity is retained for pending/applied
work; reconciled rollback starts a new host transaction. Locked reconciliation
finishes terminal backup cleanup, and a journaled cleanup removes the previous
transport's private key and envelopes before the next transfer.
Pending configuration blocks image updates, new configuration
plans and premature verification. Resume does **not** approve a merely planned
update. Discard only an unsubmitted plan:

```bash
uv run --no-sync python scripts/deploy_byteplus.py config discard \
  --workspace /private/ark-team --approve-config CONFIG_PLAN_DIGEST
```

## Rotation and Rollback

For each planned provider-key rotation:

1. Create a replacement key in the provider service through its private console or approved workflow. Keep the old key valid during planned maintenance where the service permits it.
2. Create a new **Generic secret version** in BytePlus Secret Manager containing the complete desired JSON bundle. Keep every other provider key unchanged unless you intend to replace or disable that service too. Retain the previous version for rollback.
3. Update only `version_id` in your private reference file to the exact new lowercase version UUID, keeping account, region, project and secret name unchanged.
4. Run `config plan` with that reference. Review the version and changed field names, and record the returned approval digest. Discard any prior unsubmitted plan before planning a different version.
5. Pause producers and drain active jobs, then run `config apply` with the exact digest. Use `resume` if an accepted operation is interrupted; do not delete its journal or replan around it.
6. Confirm `config status` reports the new source version with no pending transaction. Complete any separately authorized provider credential test, then restore normal traffic. Revoke the old provider key only after the new key is confirmed and the rollback window is deliberately closed.

**Creating a KMS version alone does not update the VM.** Neither editing the
reference nor changing a laptop `.env` updates it: an approved sync is required.
An image-only `update` deliberately leaves provider keys unchanged.

Roll back a **completed** sync by selecting a prior retained KMS version in
the reference and running a fresh `config plan` and approved `config apply`.
Rollback requires that the prior provider credentials are still valid.
The CLI does not create versions, rotate/revoke product keys, change version
tags or schedule rotations.

Local staging and guest runtime configuration remain **plaintext on protected
filesystems**. KMS and encrypted transfer do not replace encrypted workstation
storage or secured backups. Completed sync removes staged old/new files and
successful host backups. Interrupted or recovery-failed operations retain
private recovery material.
Plan/apply/resume also remove protected atomic-write leftovers after hard crashes.
The workspace `.deploy-????????` and host `.provider-????????` names are reserved
for these temporary files; never place operator-owned files under those names.

## Existing Deployments

Frozen workspaces and manually deployed VMs need a **separately reviewed
source/helper migration**. The CLI does not adopt them, upload new helpers to a
running VM, or weaken fingerprint checks. Do not reapply Terraform bootstrap
to force this upgrade: it can replace the VM. No current server migration is
performed by developing this integration.

## Sources

Verified 2026-11-13:

- [Generic secret management](https://docs.byteplus.com/en/docs/kms/Generic_secret_management).
- [KMS request structure and endpoint](https://docs.byteplus.com/en/docs/kms/Request_structure).
- [DescribeSecret](https://docs.byteplus.com/en/docs/kms/DescribeSecret).
- [GetSecretValue](https://docs.byteplus.com/en/docs/kms/GetSecretValue).
