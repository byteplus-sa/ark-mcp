import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const page = (path) =>
  readFileSync(new URL(`../../${path}`, import.meta.url), "utf8");

test("deployment guide distinguishes partial live provisioning from end-to-end validation", () => {
  const content = page("byteplus-terraform.md").replace(/\s+/g, " ");
  assert.match(content, /## Validation Status/);
  assert.match(content, /2026-11-13/);
  assert.match(content, /EIP quota.*before application activation/i);
  assert.match(content, /Partial Terraform state was preserved/);
  assert.match(
    content,
    /temporary VM, both disks, and security group.*removed/i,
  );
  assert.match(content, /existing team server.*unchanged/i);
  assert.match(content, /HTTPS.*resume.*updates.*not yet.*live/i);
});

test("deployment guide separates operator tools from member access", () => {
  const content = page("byteplus-terraform.md");
  assert.match(content, /## Requirements/);
  assert.match(content, /## Set Up Dependencies/);
  assert.match(content, /## Set Up IAM and Credentials/);
  assert.match(content, /members.*do not need.*IAM/i);
  assert.match(content, /ArkCLI.*bytedcli.*not required/i);
  assert.match(content, /Docker.*installed.*VM/i);
  assert.match(content, /Windows.*not supported/i);
  assert.match(content, /Git.*archive/i);
  assert.match(content, /uv python install 3\.12/);
  assert.match(content, /uv sync --locked --no-dev --python 3\.12/);
  assert.match(content, /brew install hashicorp\/tap\/terraform/);
  assert.match(content, /terraform version/);
  assert.match(content, /deploy_byteplus\.py --help/);
});

test("deployment credentials retain explicit safety and permission boundaries", () => {
  const content = page("byteplus-terraform.md");
  for (const name of [
    "BYTEPLUS_ACCESS_KEY",
    "BYTEPLUS_SECRET_KEY",
    "BYTEPLUS_SESSION_TOKEN",
    "DescribeImages",
    "DescribeKeyPairs",
    "RunCommand",
    "DescribeInvocationResults",
    "read:packages",
  ]) {
    assert.ok(content.includes(name), name);
  }
  assert.match(content, /not a validated least-privilege IAM policy/i);
  assert.match(content, /root.*guest/i);
  assert.match(content, /CLI.*does not create.*IAM/i);
  assert.match(content, /STS.*requires a new plan/);
  assert.match(content, /mode.?`?0600/);
  assert.match(content, /Do not mix raw `terraform apply`, manual disk/);
  assert.doesNotMatch(content, /export (?:BYTEPLUS_|CR_PAT)[A-Z_]*=/);
});

test("deployment shell examples parse without running installations or cloud actions", () => {
  const blocks = [
    ...page("byteplus-terraform.md").matchAll(/```bash\n([\s\S]*?)\n```/g),
  ];
  assert.ok(blocks.length > 0);
  for (const [index, block] of blocks.entries()) {
    const result = spawnSync("bash", ["-n"], {
      input: block[1],
      encoding: "utf8",
    });
    assert.equal(
      result.status,
      0,
      `Bash example ${index + 1}: ${result.stderr}`,
    );
  }
});

test("dependency and account setup links use official documentation", () => {
  const content = page("byteplus-terraform.md");
  for (const url of [
    "https://docs.astral.sh/uv/getting-started/installation/",
    "https://docs.astral.sh/uv/guides/install-python/",
    "https://developer.hashicorp.com/terraform/install",
    "https://docs.byteplus.com/en/docs/IAM/creating-an-access-key",
    "https://docs.byteplus.com/en/docs/IAM/about-roles",
    "https://docs.byteplus.com/en/docs/byteplus-platform/reference-assumerole",
    "https://docs.byteplus.com/en/docs/ecs/Creating-key-pairs",
    "https://docs.byteplus.com/en/docs/ecs/Install-the-Cloud-Assistant-client",
    "https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry",
  ]) {
    assert.ok(content.includes(url), url);
  }
  assert.match(
    page("setup/shared-server.md"),
    /\.\.\/byteplus-terraform\.md#requirements/,
  );
});

test("Secret Manager guide documents pinned operator sync and maintenance boundaries", () => {
  const content = page("secret-manager.md").replace(/\s+/g, " ");
  for (const command of ["status", "plan", "apply", "discard"]) {
    assert.ok(content.includes(`config ${command}`), command);
  }
  for (const field of [
    "region",
    "account_id",
    "project_name",
    "secret_name",
    "version_id",
  ]) {
    assert.ok(content.includes(`"${field}"`), field);
  }
  assert.match(content, /never implicitly follows.*CURRENT/);
  assert.match(content, /VM receives no KMS IAM/);
  assert.match(content, /Pause producers and drain active jobs/);
  assert.match(content, /do not prove provider credentials work/);
  assert.match(content, /live sync have not been validated/);
  assert.match(content, /omitted or empty entries disable/);
  assert.match(content, /separately reviewed source\/helper migration/);
  assert.ok(page("byteplus-terraform.md").includes("(secret-manager.md)"));
  assert.ok(page("setup/shared-server.md").includes("(../secret-manager.md)"));
  for (const block of page("secret-manager.md").matchAll(
    /```bash\n([\s\S]*?)\n```/g,
  )) {
    assert.equal(spawnSync("bash", ["-n"], { input: block[1] }).status, 0);
  }
});

test("Secret Manager setup and rotation instructions preserve unrelated service keys", () => {
  const content = page("secret-manager.md").replace(/\s+/g, " ");
  assert.match(content, /## Set Up Private Reference Files/);
  assert.match(content, /install -d -m 0700/);
  assert.match(content, /chmod 0600/);
  assert.match(content, /ModelArk.*BYTEPLUS_MODELARK_API_KEY/);
  assert.match(content, /Seed Speech.*BYTEPLUS_SEED_SPEECH_API_KEY/);
  assert.match(content, /MediaKit.*BYTEPLUS_VOD_MEDIAKIT_API_KEY/);
  assert.match(content, /Vision AI.*BYTEPLUS_CV_API_KEY/);
  assert.match(content, /1\. Create.*replacement.*provider/i);
  assert.match(content, /2\. Create a new.*Generic secret version/);
  assert.match(content, /Keep every other provider key unchanged/);
  assert.match(content, /3\. Update.*version_id/);
  assert.match(content, /4\. Run.*config plan/);
  assert.match(content, /5\. Pause producers.*config apply/);
  assert.match(content, /6\. Confirm.*config status/);
  assert.match(content, /Revoke the old provider key only after/);
  assert.match(content, /Creating a KMS version alone does not update the VM/);
});
