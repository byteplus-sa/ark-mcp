# Deploy on BytePlus with Terraform

This starter provisions **one team or organization per deployment**, with one
Ubuntu ECS host running the existing Caddy, Ark MCP, and private Redis Compose
stack. Users authenticate individually with your identity provider; BytePlus
product keys remain on the server. No GPU is needed.

The configuration is in [`deploy/terraform/byteplus/`](../deploy/terraform/byteplus/).
It is an offline-tested starter, not a live-certified deployment or an HA
architecture. `terraform apply` creates chargeable infrastructure, but does not
activate an unconfigured application.

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
private. Egress permits HTTP/HTTPS, DNS, and NTP. Public DNS records, identity
provider setup, snapshots, monitoring, and secret delivery are operator tasks.

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

## Prerequisites

- Terraform `>=1.9, <2.0`. The starter pins `byteplus-sdk/bytepluscc` to
  `0.0.63` and includes verified dependency lockfile checksums for Linux AMD64
  and macOS ARM64. Maintainers adding platforms should regenerate checksums
  with `terraform providers lock -platform=...`, not disable lock verification.
- A BytePlus account/project authorized for Cloud Control and the listed
  resource operations. Use scoped infrastructure credentials, preferably
  temporary credentials, not the application's ModelArk Bearer key.
- A verified regional Ubuntu 24.04 **AMD64** ECS image with cloud-init, an
  available AMD64 instance type, and an existing regional SSH key pair.
  Start with at least 2 vCPU/4 GiB RAM; load-test large concurrent uploads.
- A publicly resolvable hostname you control and an RS256 identity provider.
  See [identity requirements and client setup](self-hosting.md#identity-provider).
- A publicly pullable, versioned Linux AMD64 Ark MCP image. Prefer a release
  digest; `latest` and `edge` are rejected. Check availability in
  [release artifacts](releases.md) before applying.

The infrastructure `region` does not change the ModelArk endpoint region.
Choose application provider hosts and keys together.

## Configure and review

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

Edit `terraform.tfvars`: replace the image/key-pair placeholders, verify zone
and instance availability, set your actual hostname, and choose an unused
private CIDR. The example SSH source `203.0.113.10/32` is documentation-only;
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

Create a public DNS A record for `public_hostname` using `public_ip`. Do not
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

Do not overwrite an existing recovered `.env`. Populate real JWKS, issuer,
audience, tenant claim, and the product API keys you use. Generate **two
independent** secrets through your secret manager or a private host workflow:
a 64-character random hex Redis password and at least 32 random characters
for `FASTMCP_TASKS_ENCRYPTION_KEY`. Retain the encryption key securely;
changing it makes existing job snapshots unreadable.

Use literal, one-line `KEY=value` assignments. The checker rejects duplicates,
interpolation (`$`/backticks), nonregular/symlink files, wrong owner/mode, and
incomplete identity, secrets, or positive spend/rate/concurrency limits.
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
  https://YOUR_HOSTNAME/mcp
```

Expect healthy JSON and an unauthenticated `401`. Public `/ready` and `/metrics`
must remain blocked by Caddy. Then test a valid scoped JWT through an actual MCP
client, confirm model discovery, and run a supported `dry_run` through
`ark_job_submit`/`ark_job_get`. Separately verify a permitted generation,
cross-user ownership rejection, and artifact retrieval before production use.
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
real identity login, disk/EIP replacement, or live Redis failover is certified.
The pinned provider documents nested set/list update limitations; review
security-group and other nested changes carefully and inspect convergence
after every real apply.

The `BytePlus Terraform` GitHub workflow runs formatting, checksum-verified CLI
installation, provider validation, all mocked plans, and bootstrap checks for
Terraform/Compose changes. It has no cloud account credentials and performs no
real infrastructure apply.

## Research sources

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
  supports `gz+b64` file payloads used to stay under the ECS user-data limit.
- [Terraform lifecycle](https://developer.hashicorp.com/terraform/language/meta-arguments/lifecycle)
  and [mock-provider tests](https://developer.hashicorp.com/terraform/language/tests/mocking).
- [Multi-platform provider locking](https://developer.hashicorp.com/terraform/cli/commands/providers/lock):
  records verified checksums for each specified CLI platform.
