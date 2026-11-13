# Deploy on BytePlus with Terraform

This starter provisions **one team or organization per deployment**, with one
Ubuntu ECS host running the existing Caddy, Ark MCP, and private Redis Compose
stack. Members authenticate with one shared MCP key and use a fixed
`shared-team` identity; BytePlus product keys remain on the server. No GPU is needed.

The configuration is in [`deploy/terraform/byteplus/`](../deploy/terraform/byteplus/).
It has offline coverage and partial live provisioning evidence, not full
end-to-end live validation or an HA architecture. `terraform apply` creates
chargeable infrastructure, but does not activate an unconfigured application.

## Validation Status

On **2026-11-13**, the guided CLI passed live cloud and pinned-image preflight,
then created an isolated VM, disks, and security group. EIP quota blocked the
deployment before application activation. Partial Terraform state was preserved;
no provider configuration reached the guest and no paid generation ran.
The temporary VM, both disks, and security group were removed and their absence
verified through cloud APIs. The existing team server remained unchanged and
healthy.

That test exposed EBS disk sizes returned as numeric strings; the validator now
accepts canonical decimal strings while rejecting floats, booleans, malformed
values, and wrong sizes, with regression coverage. System-disk deletion also
finished after Terraform returned, so teardown must verify cloud absence rather
than rely only on an empty state file.

Guided CLI HTTPS/client checks, interruption/resume behavior, and application-only
updates have **not yet been validated live**. They remain covered by offline
tests. A fresh end-to-end test needs EIP capacity; the CLI does not automatically
reuse an unrelated IP or remove other resources to make room.

## Requirements

**These requirements are for the deployment operator. Team members do not need IAM
credentials, Terraform, Python, or a local checkout.** Members need only a trusted
HTTPS endpoint, the shared MCP key, and a compatible client. See
[Connect to a Shared Server](setup/shared-server.md).

| Requirement | Needed where | How to prepare |
|---|---|---|
| Trusted macOS or Linux workstation | Operator | The CLI uses POSIX file permissions and locking. Native Windows is not supported. |
| Reviewed source checkout | Operator | Obtain it with Git or extract a reviewed source archive. It must contain `scripts/deploy_byteplus.py`; installing the MCP wheel alone does not install this CLI. |
| Python 3.12+ and uv | Operator | Install uv, then let uv install Python and the locked project environment, as shown below. |
| Terraform `>=1.9, <2.0` | Operator | Install the CLI on `PATH`. Terraform installs the pinned BytePlus provider automatically. |
| BytePlus IAM credentials | Operator | Scoped infrastructure AK/SK, preferably temporary STS credentials, plus the permissions below. Provider bearer API keys are not infrastructure credentials. |
| Cloud account, quota, and guest-agent access | BytePlus | Active billing, resource availability, registered SSH public key, Ubuntu AMD64 image, and working ECS Cloud Assistant. |
| Compatible container image | VM, checked by operator | Digest-pinned Linux AMD64 GHCR image supporting `MCP_AUTH_MODE=api_key`; private packages also need a pull token. |
| Product API keys | Private provider file or pinned Secret Manager version, then VM | Keys for the capabilities you enable. See [Provider Keys](api-keys.md) and [Secret Manager](secret-manager.md). |
| Protected state and backups | Operator and VM | Private local deployment workspace, encrypted storage, and tested off-host backups. Retained storage is not a backup. |

**ArkCLI and bytedcli are not required.** The CLI uses signed BytePlus API
requests and Terraform; ArkCLI SSO is not automatically deployment
authentication. An existing organization-approved credential tool, including
bytedcli where available, can supply temporary credentials, but the deployer
does not install it or log in for you.
GitHub CLI, Node.js, npm, and local Docker are also unnecessary for deployment.
Docker and Compose are installed on the VM by bootstrap; Node/npm are needed
only when developing this documentation website.

## Set Up Dependencies

### Install uv and Python

On macOS with Homebrew already installed:

```bash
brew install uv
```

On macOS or Linux without Homebrew, use the
[official uv installation guide](https://docs.astral.sh/uv/getting-started/installation/).
Review the installer before executing it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | less
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Open a new terminal or follow the installer's `PATH` instructions, then:

```bash
uv --version
uv python install 3.12
```

Python does not have to be installed beforehand. See
[uv's Python setup guide](https://docs.astral.sh/uv/guides/install-python/).
If you cannot use these installers, follow your organization's approved
package-distribution process; do not disable TLS verification.

### Install Terraform

On macOS with Homebrew:

```bash
brew tap hashicorp/tap
brew install hashicorp/tap/terraform
terraform version
```

On Linux, follow HashiCorp's
[official installation instructions](https://developer.hashicorp.com/terraform/install)
for its apt/yum repository or download the binary for your workstation.
For direct downloads, follow
[HashiCorp binary verification](https://developer.hashicorp.com/well-architected-framework/operational-excellence/verify-hashicorp-binary)
before adding the executable to `PATH`.
Run `terraform version` and confirm **1.9 or newer, below 2.0**. Package managers
may change their default version; an unsupported version must be replaced with
a supported release before deployment.

The starter pins `byteplus-sdk/bytepluscc` to `0.0.63`; do not install a provider
manually or replace it with the legacy BytePlus provider. The checked-in
lockfile includes verified checksums for Linux AMD64 and macOS ARM64.
Maintainers adding platforms must regenerate verified checksums with
`terraform providers lock -platform=...`, not bypass lock verification.

### Prepare the Checkout

Use a reviewed checkout containing the deployment CLI, not an arbitrary old
release. In particular, do not assume `v0.2.0` includes this CLI or a compatible
shared-key container. Git setup is documented by the
[official Git downloads page](https://git-scm.com/downloads); a reviewed source
archive is an alternative.

Run these commands from the checkout root:

```bash
uv sync --locked --no-dev --python 3.12
uv run --no-sync python --version
uv run --no-sync python scripts/deploy_byteplus.py --help
```

These commands install the locked Python dependencies and display help; they
do not provision resources, start the MCP server, or read provider keys.
If the help command cannot find the script, your checkout does not contain
the deployment CLI. Resolve that revision mismatch before proceeding.

## Set Up IAM and Credentials

### Arrange the Deployment Identity

Ask your account administrator to create or approve a dedicated deployment
IAM user or an assumable role. Prefer temporary STS credentials; avoid main
account keys. Official setup references:

- [IAM access keys and permission assignment](https://docs.byteplus.com/en/docs/IAM/about-access-keys).
- [Create an IAM user's access key](https://docs.byteplus.com/en/docs/IAM/creating-an-access-key).
- [IAM roles](https://docs.byteplus.com/en/docs/IAM/about-roles) and
  [AssumeRole temporary credentials](https://docs.byteplus.com/en/docs/byteplus-platform/reference-assumerole).

For an IAM user, an administrator can open **IAM > User management > User**,
select the dedicated user, assign approved policies, and create an access key
on its key tab. Store the key directly in a secret manager. For a role, use
your approved STS credential workflow; the CLI does not assume the role itself.

### Required Permission Capabilities

| Purpose | Permissions to review with your administrator |
|---|---|
| Terraform provisioning | Cloud Control operations and underlying create/read/configure/attach/tag operations for ECS, EBS, VPC, subnets, security groups, and EIP in the selected project and region. Existing-network mode still creates ECS, storage, a security group, and an EIP. |
| ECS preflight and identity checks | `DescribeImages`, `DescribeAvailableResource`, `DescribeKeyPairs`, and `DescribeInstances`. |
| Network preflight | `DescribeVpcs`, `DescribeSubnets`, and `DescribeEipAddresses`. |
| Storage preflight and identity checks | `DescribeVolumeType` and `DescribeVolumes`. |
| Guest configuration and updates | `RunCommand` and `DescribeInvocationResults` on the deployment's ECS instance. Commands run as **root** in the guest. |
| Optional Secret Manager source | KMS `DescribeSecret` and `GetSecretValue` on the intended generic secret; review secret/project scoping and encryption-key access with your administrator. |

**This is a capability checklist, not a validated least-privilege IAM policy.**
The API names are not a complete copyable policy: your administrator must
validate Cloud Control permissions, service action names, resource scoping,
and first-create permissions against the pinned provider. Scope access to
the intended project/resources where supported. Do not grant account-wide
administrator access merely to make a failed preflight pass.

On first ECS use, an account administrator may also need to complete
[ECS cross-service authorization](https://docs.byteplus.com/en/docs/ecs/Cross-service_authorization)
in the console. This account setup is separate from routine deployment
permissions. The CLI does not create IAM users, attach policies, create service
roles, or grant itself additional permissions.

### Load Infrastructure Credentials

The guided CLI requires these environment variables in the operator's shell:

| Variable | Requirement |
|---|---|
| `BYTEPLUS_ACCESS_KEY` | Infrastructure access key ID. |
| `BYTEPLUS_SECRET_KEY` | Matching infrastructure secret access key. |
| `BYTEPLUS_SESSION_TOKEN` | Required with STS credentials; omit/unset for long-lived IAM user keys. |

Use a secret manager or approved credential injector. As an interactive
fallback, start **Bash** (`bash` on macOS if your default shell is zsh), then
enter existing scoped credentials at hidden prompts rather than typing their
values in commands:

```bash
set +x
read -r -s -p "BytePlus access key: " BYTEPLUS_ACCESS_KEY
printf '\n'
read -r -s -p "BytePlus secret key: " BYTEPLUS_SECRET_KEY
printf '\n'
export BYTEPLUS_ACCESS_KEY BYTEPLUS_SECRET_KEY
```

For STS credentials, also enter the matching session token:

```bash
read -r -s -p "BytePlus session token: " BYTEPLUS_SESSION_TOKEN
printf '\n'
export BYTEPLUS_SESSION_TOKEN
```

For a long-lived IAM user key instead, run `unset BYTEPLUS_SESSION_TOKEN` to
avoid accidentally retaining an unrelated token. Keep deploying from this
same shell; environment variables are not a secret vault. Do not use shell
tracing, echo credentials, paste them into chat, or put them in HCL/tfvars/JSON.
The raw Terraform provider also supports profiles, but **profile-only
authentication is insufficient for the guided CLI's signed API preflight**.
See the [pinned provider authentication reference](https://github.com/byteplus-sdk/terraform-provider-bytepluscc/blob/v0.0.63/docs/index.md).
Refreshing STS credentials between `plan` and `deploy` requires a new plan.

### Prepare Product and Registry Credentials

Prefer the [Secret Manager provider-key workflow](secret-manager.md) to avoid
maintaining a separate provider-key input file. Alternatively, keep product API keys in a separate same-owner regular mode-`0600` file
outside the checkout, entered through a private editor or secret manager.
The provider file is not an IAM credential file. Only the supported product
keys and settings listed in the guided workflow are copied to the VM.
The shared MCP team key is generated by deployment; do not reuse a provider
key as the team key.

For a private GHCR image, create a **personal access token (classic)** with
`read:packages`, from an identity that can read that package. Authorize it
for organization SSO if required. Neither `write:packages` nor
`delete:packages` is needed for pulling.
Follow [GitHub's Container registry authentication guide](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).
Store only the token in a same-owner regular mode-`0600` file; provide its
path with `--registry-token-file` and your username with `--registry-user`.
Local `docker login` is not required and does not replace these explicit flags.
Public images can be checked and pulled anonymously.

## Check Cloud Prerequisites

- Confirm billing and quota for ECS, a system disk, retained EBS storage, VPC,
  subnet, security group, and EIP. Preflight is not a quota reservation or cost
  estimate. An existing subnet must belong to the supplied VPC and selected
  availability zone; both resources must be in the selected region.
- Select a regional Ubuntu 24.04 **AMD64** image with cloud-init and working
  ECS Cloud Assistant, plus an available AMD64 instance type. Start with at
  least 2 vCPU/4 GiB RAM; no GPU is required.
  See [Cloud Assistant installation](https://docs.byteplus.com/en/docs/ecs/Install-the-Cloud-Assistant-client).
  The CLI does not install or repair a missing Cloud Assistant agent; guest
  command access must work before encrypted configuration can be delivered.
- Register an SSH public key pair in the same region and provide its exact
  name. See [Creating key pairs](https://docs.byteplus.com/en/docs/ecs/Creating-key-pairs).
  Terraform does not create or accept a private SSH key. The guided CLI uses
  the guest agent, not SSH, and defaults to no SSH ingress; keep a separately
  approved console/bastion recovery route.
- Select a digest-pinned Linux AMD64 Ark MCP image **verified to support
  `MCP_AUTH_MODE=api_key`**. `latest` and `edge` are rejected. The published
  `v0.2.0` image is JWT-only and incompatible. Check
  [release artifacts](releases.md) for a compatible image before applying;
  synthetic test digests and the example placeholders are not usable images.
- Allow workstation HTTPS access to BytePlus APIs, Terraform/package
  distribution endpoints, and GHCR. The VM needs outbound HTTP/HTTPS, DNS,
  NTP, package repositories, the guest-agent service, and enabled provider
  hosts. Do not expose application port 3000 or Redis port 6379.
- Allow public TCP 80 and HTTPS access for ACME and clients. No purchased domain
  is required with `public_hostname=null`; custom DNS must resolve to the new
  EIP. Generic DNS depends on sslip.io and public ACME availability.

The infrastructure `region` does not change the ModelArk endpoint region.
Choose application provider hosts and keys together.

## Guided Deployment CLI

The checkout includes `scripts/deploy_byteplus.py`: a repeatable CLI around
Terraform, encrypted ECS-agent configuration, activation, and verification.
It is not yet a one-click web wizard. No existing deployment is automatically
adopted or changed. Complete the [requirements](#requirements),
[dependency setup](#set-up-dependencies), and [IAM setup](#set-up-iam-and-credentials)
above first. Preflight checks current access and availability, not guaranteed quota,
capacity, pricing, or provider generation authorization.

The `/private/...` paths below are examples, not directories supplied by the CLI.
Replace them with actual absolute paths on your workstation. For example, create
a private parent directory under your home directory:

```bash
install -d -m 0700 "$HOME/.config/ark-mcp-deploy"
```

Create the config/provider/token files there through a private editor or secret
manager, with mode `0600`, and use a **new, not-yet-created** child path for the
CLI workspace. Do not put these files in Git, a shared folder, or the public
documentation build.

Create a nonsecret JSON configuration outside the checkout, mode `0600`:

```json
{
  "name": "ark-mcp-team",
  "region": "ap-southeast-1",
  "zone_id": "ap-southeast-1a",
  "image_id": "image-REPLACE",
  "instance_type": "ecs.c3i.large",
  "key_pair_name": "REPLACE-WITH-REGISTERED-PUBLIC-KEY",
  "ark_mcp_image": "REPLACE-WITH-VERIFIED-GHCR-SHA256-DIGEST",
  "public_hostname": null
}
```

The placeholders intentionally fail preflight until replaced. CLI defaults
are a 40-GiB retained disk, 40-GiB system disk, deletion protection, no SSH
ingress, dedicated VPC, and one replica. The raw module retains its 100-GiB
state-disk default. Set `create_vpc=false`, `existing_vpc_id` and
`existing_subnet_id` explicitly to use an existing network. Unknown fields and
credentials in JSON are rejected. `public_hostname=null` uses
`ark-mcp.<EIP-with-dashes>.sslip.io`; custom DNS must resolve to the new EIP.
Generic DNS depends on sslip.io and public ACME availability.

```bash
uv run --no-sync python scripts/deploy_byteplus.py init \
  --config /private/deployment.json --workspace /private/ark-team
uv run --no-sync python scripts/deploy_byteplus.py plan --workspace /private/ark-team
uv run --no-sync python scripts/deploy_byteplus.py deploy --workspace /private/ark-team \
  --provider-env-file /private/providers.env --approve-plan PLAN_DIGEST
```

`init` requires a **new** workspace and creates it mode `0700`; files are
`0600`. `plan` validates cloud resources and GHCR image metadata, freezes the
templates, and returns resource actions, ongoing-charge notice and an approval
digest. `deploy` applies that exact saved plan. Interactive approval requires
typing the deployment name; noninteractive approval requires the exact digest.
The digest binds source/configuration, binary plan, current state and current
access key. Refreshing STS credentials between plan/apply requires a new plan.
Deletion and replacement plans are refused. There is no destroy/import/adoption
command and no automatic quota workaround.
The CLI forces Terraform's default workspace and rejects pre-existing or named
workspace state; each team must use its own newly initialized CLI directory.

Private GHCR images need explicit pull credentials on `plan` and first `deploy`:
add `--registry-user USER --registry-token-file /private/ghcr.token`, with a
same-owner mode-`0600` token file. The image must be digest-pinned, Linux AMD64
and explicitly default to shared-key auth. Login is temporary, and the image
is pulled before credentials are removed. Failed private-image pulls can use
fresh credentials without rewriting runtime configuration.

The provider file must also be a same-owner regular mode-`0600` file. Only the
four product bearer keys, ModelArk/Seed Audio base URLs, supported 3D/OmniHuman/
Premium flags and spend/rate/concurrency/retention limits are copied.
IAM, JWT, object-storage credentials and custom model bindings are **not**
copied. This CLI creates three independent secrets once: team key, Redis
password and job-snapshot encryption key. Subsequent runs preserve them.
Provider keys, raw team keys and registry tokens never enter Terraform or
plaintext ECS command content. Transfer uses a guest-only RSA key and
instance-bound AES-GCM ciphertext. Validated configuration is published
atomically without overwriting an existing `.env`; matching config repairs
the durable receipt, while mismatches fail closed.
Transfer keys/chunks are root-private on the retained disk until cleanup, so a
guest reboot does not discard accepted transfer progress. An explicitly
reconciled failed transfer can start a fresh encrypted attempt without changing
the original runtime secrets.

After apply, the CLI verifies instance/disk identities, explicitly initializes
only the new empty disk, mounts by UUID, configures the host and activates.
An existing filesystem without the matching marker requires manual recovery,
never formatting. It then checks HTTPS health, missing/wrong keys, untrusted
Origin denial, blocked internal routes, two clients' discovery, and a shared
Seedream background `dry_run` when available. **No paid generation runs.**
The result gives the MCP URL and private `team.key` path, never the key.
Deliver that key through a secret manager.

### Resume And Update

```bash
uv run --no-sync python scripts/deploy_byteplus.py status --workspace /private/ark-team
uv run --no-sync python scripts/deploy_byteplus.py resume --workspace /private/ark-team
uv run --no-sync python scripts/deploy_byteplus.py verify --workspace /private/ark-team
uv run --no-sync python scripts/deploy_byteplus.py update --workspace /private/ark-team \
  --image GHCR_SHA256_DIGEST --approve-update
```

Accepted ECS command IDs are saved immediately and polled on resume. A journal
entry with unknown acceptance blocks replay and requires operator
reconciliation. Failed repeatable guest commands require explicit
`--retry-failed` after investigation. Interrupted/partial Terraform applies
retain state; a known failed apply needs a fresh reviewed plan, while an
unknown apply outcome requires manual reconciliation. Never erase the journal
or regenerate keys to get past a failure.

`update` pre-pulls the image, changes only the MCP image in installed Compose,
drains/recreates only MCP, and preserves private config, SQLite, Redis, media,
TLS state and job encryption. It does **not** modify Terraform bootstrap
inputs. Failed updates attempt restoration of the original Compose/image;
inspect health if restoration also fails. Repeat the same `update` command
to poll an accepted pending update. The guest timeout is 2400 seconds,
including forward and rollback grace periods; poll timeout preserves its ID.
Private images require fresh registry file flags unless already pulled.
`resume` also dispatches a recorded pending update automatically.

Frozen-source changes require a separately reviewed migration, not an
automatic bootstrap upgrade. Each completed verification round starts a new
nonbillable job; interrupted rounds retain their job ID and never resubmit it.
An expired/deleted pending job needs operator reconciliation. Keep the entire private
workspace, its Terraform state and matching secrets together. Filesystem
permissions and encryption in transit do not encrypt local state at rest.
Use encrypted storage and tested off-host backups. Automated backups, remote
state, account login, exact pricing, resource adoption and a browser wizard
remain follow-ups. Offline CLI validation is not live deployment certification.

### Secret Manager Provider Keys

The CLI supports pinned-version, operator-side BytePlus Secret Manager reads
for first deployment and approved provider-only configuration sync:
`config status`, `config plan`, `config apply`, and `config discard`.
Follow [Secret Manager setup and rotation](secret-manager.md) for account/project
binding, scoped IAM access, protected reference files, maintenance approval,
resume and rollback. Secret Manager access stays on the operator workstation;
the VM receives no IAM credentials and retains its private runtime `.env`.

This path is offline-tested, **not live-validated**. Existing frozen workspaces
and manually deployed VMs need a separately reviewed source/helper migration;
they are never automatically adopted or upgraded.

## What it creates

| Resource | Behavior |
|---|---|
| VPC and subnet | Dedicated network by default, or reuse a supplied VPC/subnet in the selected zone |
| Security group | Public TCP 80 for ACME/redirect; TCP/UDP 443 from `https_cidrs`; SSH disabled unless narrow administrator CIDRs are supplied |
| ECS | One Ubuntu 24.04 AMD64 host, existing SSH key pair, 40 GiB system disk, deletion protection on |
| EIP | Traffic-billed public IP with a bandwidth cap; not automatically released when ECS is deleted |
| EBS state disk | 100 GiB by default, separate from the system disk, retained on ECS deletion and protected against Terraform destruction |
| TOS bucket | Optional, private, protected against Terraform destruction; application credentials and lifecycle rules are not configured |
| Guest bootstrap | Installs Ubuntu Docker/Compose packages and deployment files; never formats disks or embeds application secrets |

Only Caddy publishes ports. Application port 3000 and Redis port 6379 are
private. Egress permits HTTP/HTTPS, DNS, and NTP. Public DNS records,
snapshots, monitoring, and private key delivery are operator tasks.

The application uses SQLite **inside its Python process**, at
`/app/.artifacts/runtime.sqlite3`. Bind-backed Docker volumes place state on
the retained disk:

| Host path | Contents |
|---|---|
| `/srv/ark-mcp/ark-state` | SQLite ownership/budget/execution state and filesystem artifacts |
| `/srv/ark-mcp/redis-data` | Redis append-only queue and retained job data |
| `/srv/ark-mcp/caddy-data`, `/srv/ark-mcp/caddy-config` | TLS certificate and Caddy state |
| `/srv/ark-mcp/config/.env` | Root-owned private runtime configuration |

Docker engine metadata and images remain on the system disk; the application
data does not. Keep **one application replica**. Redis does not distribute
SQLite or in-process limiters.

## Configure and review

This section and the host-preparation instructions below describe the
**manual Terraform alternative**. The guided CLI performs those steps itself
using its private workspace. Do not mix raw `terraform apply`, manual disk
initialization, or manual key generation into a CLI-managed deployment.

Authenticate outside Terraform using a secured credential injector or the
provider's existing profile. The provider supports `BYTEPLUS_ACCESS_KEY`,
`BYTEPLUS_SECRET_KEY`, and optional `BYTEPLUS_SESSION_TOKEN`, or
`BYTEPLUS_PROFILE`/`BYTEPLUS_FILE_PATH`. Do not paste values into HCL, tfvars,
shell history, or logs. ArkCLI sign-in is not automatically a provider profile.

From the repository root:

```bash
cp deploy/terraform/byteplus/terraform.tfvars.example \
  deploy/terraform/byteplus/terraform.tfvars
terraform -chdir=deploy/terraform/byteplus init
terraform -chdir=deploy/terraform/byteplus validate
terraform -chdir=deploy/terraform/byteplus test
```

Edit `terraform.tfvars`: replace the ECS `image_id` and `key_pair_name`
placeholders, and replace `ark_mcp_image` with a verified shared-key-capable
image digest or release tag. Verify zone and instance availability, set your
actual hostname or `null`, and choose an unused private CIDR.
The example SSH source `203.0.113.10/32` is documentation-only;
replace it with your administrator public IP, or use `admin_cidrs=[]` and
arrange a working console/bastion access method. Terraform never creates an
SSH password or private key.

For an existing network, also set:

```hcl
create_vpc         = false
existing_vpc_id    = "vpc-REPLACE"
existing_subnet_id = "subnet-REPLACE"
```

The subnet must belong to the supplied VPC and selected availability zone.
This mode does not manage or destroy that existing network.

```bash
terraform -chdir=deploy/terraform/byteplus plan -out=deployment.tfplan
```

Review resources, charges, network rules, and any replacements before
authorizing:

```bash
terraform -chdir=deploy/terraform/byteplus apply deployment.tfplan
terraform -chdir=deploy/terraform/byteplus output
```

Use one secured state backend and working directory per deployment, with
locking, encryption, and access controls. This starter deliberately selects
no remote backend. Local state is not a backup strategy; protect it and its
backups. State, plans, provider caches, and tfvars are gitignored, not encrypted
by `.gitignore`. Never enable verbose Terraform logging with real credentials.

## Prepare the host

For custom DNS, create a public A record for `public_hostname` using `public_ip`. Do not
create an AAAA record unless you separately provision working IPv6 ingress.

Connect using your configured SSH key or console access. Check installation:

```bash
sudo cloud-init status --wait
sudo systemctl status ark-mcp-install.service
sudo journalctl -u ark-mcp-install.service --no-pager
```

The installer retries if outbound access is not ready while the EIP attaches.
It rejects unsupported OS/architecture and leaves the stack inactive.
`cloud-init` completion alone does not prove package installation succeeded.

### Initialize only a genuinely new disk

Use the `state_volume` output, console attachment information, and guest
device metadata to identify the retained EBS disk. Device names are hints,
not stable identities. Never assume `/dev/vdb`, and never initialize the
system disk or a recovered state disk.

```bash
sudo lsblk -o NAME,SIZE,TYPE,FSTYPE,UUID,MOUNTPOINTS,SERIAL
STATE_DEVICE=/dev/REPLACE_WITH_VERIFIED_STATE_DISK
sudo blkid "$STATE_DEVICE"
sudo wipefs --no-act "$STATE_DEVICE"
```

Stop if the device has an existing filesystem, partition table, or signatures.
For a **confirmed new, empty** data disk only, create the filesystem:

```bash
sudo mkfs.ext4 "$STATE_DEVICE"
```

For a retained or restored disk, skip formatting and use the filesystem's
existing device/partition and UUID. Verify the UUID is not already in
`/etc/fstab`, then register and mount it:

```bash
STATE_UUID=$(sudo blkid -s UUID -o value "$STATE_DEVICE")
test -n "$STATE_UUID"
sudo install -d -m 0755 /srv/ark-mcp
printf 'UUID=%s /srv/ark-mcp ext4 defaults,nofail 0 2\n' "$STATE_UUID" \
  | sudo tee -a /etc/fstab
sudo mount /srv/ark-mcp
sudo findmnt /srv/ark-mcp
```

The example assumes an ext4 filesystem on that exact device. Adapt the
filesystem type and partition for restored disks. `nofail` allows the host
to boot for repair, but both Docker and Ark MCP refuse to start without the
mount. Verify the mounted UUID, not merely the existence of the directory.

### Install private configuration

For a new deployment only:

```bash
sudo install -d -m 0700 /srv/ark-mcp/config
sudo install -m 0600 -o root -g root /opt/ark-mcp/.env.example \
  /srv/ark-mcp/config/.env
sudoedit /srv/ark-mcp/config/.env
```

Do not overwrite an existing recovered `.env`. Use `MCP_AUTH_MODE=api_key`,
the default in Docker, Compose and Terraform.
Generate the key on the host (or use the repository's
`scripts/generate_mcp_key.py` on a trusted POSIX workstation):

```bash
sudo python3 /opt/ark-mcp/generate_mcp_key.py /root/ark-mcp-team.key
```

Set the emitted `MCP_API_KEY_SHA256` assignment in the private `.env`.
The raw key goes only to members through a secret manager, not into Terraform
or server configuration. See [shared-key setup and rotation](self-hosting.md#shared-mcp-key).
Use the shared-key-capable application image selected before applying;
`v0.2.0` and earlier JWT-only images reject `api_key` mode.
Former JWT deployments must follow the
[breaking-change migration](self-hosting.md#breaking-change-migration);
there is no automatic ownership transfer.

Populate the product API keys you use. For a new deployment, generate **two
independent** secrets through your secret manager or a private host workflow:
a 64-character random hex Redis password and at least 32 random characters
for `FASTMCP_TASKS_ENCRYPTION_KEY`. Retain the encryption key securely;
changing it makes existing job snapshots unreadable.

Use literal, one-line `KEY=value` assignments. The checker rejects duplicates,
interpolation (`$`/backticks), nonregular/symlink files, wrong owner/mode, and
incomplete shared-key configuration, secrets, or positive spend/rate/concurrency limits.
It is a preflight check, not proof that credentials work or secrets are random.
Never source `.env`, print it, or render the full Compose configuration into logs.

If you selected `tos_bucket_name`, configure narrowly scoped TOS credentials
privately and a bucket lifecycle policy matching your retention requirement.
Only then set `ARTIFACT_BACKEND=object_storage`. Creating the bucket does not
migrate existing filesystem media or remove the need for SQLite/Redis backups.
The application sweeper does not delete bucket objects.

```bash
sudo /opt/ark-mcp/activate.sh
sudo systemctl status ark-mcp.service
sudo docker compose --env-file /srv/ark-mcp/config/.env \
  -f /opt/ark-mcp/compose.yaml ps
```

Activation validates the mount and configuration, creates data directories
(application state uses UID/GID 1001), and starts the stack with health waiting.
Both Docker daemon startup and the application unit check the mount and private
configuration, in that order, before starting. This also gates Docker's automatic
restart of saved containers after an unclean reboot. It does not halt already
running containers when configuration is edited or deleted: stop and drain the
stack while the old configuration is valid, then edit and activate deliberately.
If a startup guard fails, restore the verified mount and valid configuration,
run `sudo systemctl reset-failed docker.service ark-mcp.service`, then activate.

## Verify before inviting users

```bash
curl --fail --silent --show-error https://YOUR_HOSTNAME/health
curl --silent --output /dev/null --write-out '%{http_code}\n' \
  --request POST \
  --header 'Content-Type: application/json' \
  --header 'Accept: application/json, text/event-stream' \
  --data '{"jsonrpc":"2.0","id":1,"method":"ping"}' \
  https://YOUR_HOSTNAME/mcp
```

Expect healthy JSON and an unauthenticated POST `401`. Stateless MCP disables
GET `/mcp`, which returns `405` instead of testing bearer authentication.
Public `/ready` and `/metrics`
must remain blocked by Caddy. Then test the shared key through an actual MCP
client, confirm model discovery, and run a supported `dry_run` through
`ark_job_submit`/`ark_job_get`. Separately verify a permitted generation,
artifact retrieval, and the shared ownership policy before production use:
members must be able to access the same
team jobs, while account-wide admin operations remain denied. Verify a wrong key
returns `401`, and repeat after rotation to prove the old key is rejected.
Paid generation requires an explicitly approved test budget.

Reboot during an approved maintenance window and confirm the expected EBS UUID
mounts, Docker/Ark MCP resume, and SQLite/Redis data remains accessible. Include
a restore drill and your workload's concurrency/large-upload tests.
On a disposable Linux deployment, also test an unclean restart with missing or
invalid private configuration: saved containers must not become available.
Restore valid configuration and confirm recovery. Graceful shutdown alone can
mask this path because it explicitly stops the containers.

## Backup, upgrade, and recovery

- Stop admitting work and drain during maintenance. `sudo systemctl stop
  ark-mcp.service` stops all three services with a bounded grace period. An
  interrupted paid submission is not guaranteed to finish or replay safely.
- Snapshot/back up the retained disk with the stack stopped for coordinated
  SQLite, Redis, and media state. Keep the encryption key and private config
  in a protected secret backup. Protect snapshot access and test restores.
- Preserve SQLite, Redis, and the matching encryption key together. After an
  interrupted submission, reconcile saved provider task IDs before resubmitting;
  there is no automatic exactly-once paid-provider guarantee.
- Updating the Terraform image, rendered Compose, scripts, or other bootstrap
  content **plans ECS replacement**, not an in-place application upgrade.
  Deletion protection blocks that replacement. Review the complete plan first.
- For a planned replacement, back up and stop the stack; disable ECS deletion
  protection in a separate apply without other changes. Then review replacement
  and retained disk/EIP behavior. Mount the existing disk by UUID, restore
  `/etc/fstab`, preserve `.env`, and activate only after attachment verification.
  Do not format. Do not run two applications against the same state.
- Provider disk/EIP reattachment and post-apply convergence have not been
  live-verified. `prevent_destroy` will block any disk/bucket replacement that
  Terraform requires. Investigate that plan rather than removing the guard
  as a routine upgrade step.

Container recreation using the same mounted disk retains data. Host system-disk
loss requires rebuilding the guest deployment files and remounting retained
state. Volume deletion, corruption, and region/zone loss require backups.

## Decommissioning and limits

Ordinary `terraform destroy` is intentionally blocked by disk/bucket
`prevent_destroy` and ECS deletion protection. Disabling ECS protection does
not disable the storage guards. Do not remove resource blocks casually:
`prevent_destroy` is a configuration guard, not a permanent cloud retention
policy. Retaining the EIP on ECS deletion also does not prevent Terraform from
explicitly destroying its EIP resource.

Choose archival/retention and backup requirements before decommissioning.
Use a reviewed state/import or ownership-transfer procedure for retained
resources; remove guards only for explicitly authorized deletion. Check for
ongoing retained-disk, EIP, TOS, snapshot, and traffic charges afterward.

Current validation covers provider schema, mocked new/existing-network plans,
negative inputs, compressed cloud-init rendering, Compose parsing, and host
preflight behavior. No BytePlus apply, guest package installation, ACME issuance,
client shared-key access/rotation, disk/EIP replacement, or live Redis failover is certified.
The pinned provider documents nested set/list update limitations; review
security-group and other nested changes carefully and inspect convergence
after every real apply.

The `BytePlus Terraform` GitHub workflow runs formatting, checksum-verified CLI
installation, provider validation, all mocked plans, and bootstrap checks for
Terraform/Compose changes. It has no cloud account credentials and performs no
real infrastructure apply.

## Research sources

Dependency installation, IAM/STS, ECS setup, and GHCR links in the setup
sections above were checked on 2026-11-13. Local CLI version and permission
requirements are derived from this checkout; external installation guides
do not certify a deployment or supply a validated IAM policy.

Verified 2026-11-11:

- [BytePlus Cloud Control provider v0.0.63](https://registry.terraform.io/providers/byteplus-sdk/bytepluscc/0.0.63/docs)
  and [versioned authentication/update cautions](https://github.com/byteplus-sdk/terraform-provider-bytepluscc/blob/v0.0.63/docs/index.md).
- Versioned resource contracts:
  [ECS](https://github.com/byteplus-sdk/terraform-provider-bytepluscc/blob/v0.0.63/docs/resources/ecs_instance.md),
  [EBS](https://github.com/byteplus-sdk/terraform-provider-bytepluscc/blob/v0.0.63/docs/resources/storageebs_volume.md),
  [EIP](https://github.com/byteplus-sdk/terraform-provider-bytepluscc/blob/v0.0.63/docs/resources/vpc_eip.md),
  [security group](https://github.com/byteplus-sdk/terraform-provider-bytepluscc/blob/v0.0.63/docs/resources/vpc_security_group.md),
  [TOS](https://github.com/byteplus-sdk/terraform-provider-bytepluscc/blob/v0.0.63/docs/resources/tos_bucket.md).
  The installed schema requires EBS `pay_type="post"` despite the documentation's
  capitalized example.
- [Canonical cloud-init write_files implementation](https://github.com/canonical/cloud-init/blob/main/cloudinit/config/cc_write_files.py):
  supports file payloads. The complete cloud-config uses gzip-compressed user
  data, with a bundled helper archive, to fit the provider's encoded 16-KiB limit.
- [Canonical user-data formats](https://cloudinit.readthedocs.io/en/latest/explanation/format.html)
  and [gzip processing](https://github.com/canonical/cloud-init/blob/main/cloudinit/user_data.py),
  verified 2026-11-13.
- [BytePlus SDK v1.0.68](https://github.com/byteplus-sdk/byteplus-go-sdk-v2/tree/v1.0.68/service)
  for signed ECS/VPC/storage preflight and root command invocation.
- [Registry V2 API](https://github.com/distribution/distribution/blob/main/docs/content/spec/api.md)
  for digest integrity and explicit GHCR pull authentication.
- [Terraform lifecycle](https://developer.hashicorp.com/terraform/language/meta-arguments/lifecycle)
  and [mock-provider tests](https://developer.hashicorp.com/terraform/language/tests/mocking).
- [Multi-platform provider locking](https://developer.hashicorp.com/terraform/cli/commands/providers/lock):
  records verified checksums for each specified CLI platform.
