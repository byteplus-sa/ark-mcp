mock_provider "bytepluscc" {
  mock_resource "bytepluscc_vpc_vpc" {
    defaults = { vpc_id = "vpc-test" }
  }
  mock_resource "bytepluscc_vpc_subnet" {
    defaults = { subnet_id = "subnet-test" }
  }
  mock_resource "bytepluscc_vpc_security_group" {
    defaults = { security_group_id = "sg-test" }
  }
  mock_resource "bytepluscc_ecs_instance" {
    defaults = { instance_id = "i-test" }
  }
  mock_data "bytepluscc_vpc_subnet" {
    defaults = {
      vpc_id  = "vpc-existing"
      zone_id = "ap-southeast-1a"
    }
  }
}

variables {
  zone_id         = "ap-southeast-1a"
  image_id        = "image-test"
  instance_type   = "ecs.g4i.large"
  key_pair_name   = "test-public-keypair"
  public_hostname = "mcp.team.test"
  ark_mcp_image   = "registry.test/ark-mcp@sha256:${sha256("mock-only-image-not-for-deployment")}"
}

run "dedicated_network" {
  command = plan

  assert {
    condition     = length(bytepluscc_vpc_vpc.this) == 1 && length(bytepluscc_vpc_subnet.this) == 1
    error_message = "Default deployment must create one VPC and one subnet."
  }
  assert {
    condition     = bytepluscc_storageebs_volume.state.delete_with_instance == false
    error_message = "State must not be deleted with the VM."
  }
  assert {
    condition     = bytepluscc_ecs_instance.this.deletion_protection && bytepluscc_vpc_eip.this.release_with_instance == false
    error_message = "VM deletion protection and retained EIP must be enabled."
  }
  assert {
    condition     = alltrue([for rule in bytepluscc_vpc_security_group.this.ingress_permissions : contains([80, 443], rule.port_start)])
    error_message = "Only web ports are public; SSH defaults to disabled."
  }
  assert {
    condition     = length(bytepluscc_tos_bucket.media) == 0
    error_message = "Object storage must remain optional."
  }
  assert {
    condition     = local.compose.services["ark-mcp"].environment.MCP_AUTH_MODE == "$${MCP_AUTH_MODE:-api_key}" && !contains(keys(local.compose.services["ark-mcp"]), "build")
    error_message = "Use the pinned image with shared-key HTTP authentication."
  }
  assert {
    condition     = contains(keys(local.bootstrap_files), "/opt/ark-mcp/generate_mcp_key.py") && strcontains(local.environment_example, "MCP_API_KEY_SHA256=\n")
    error_message = "Include key generation but never generate or store keys in Terraform."
  }
  assert {
    condition     = alltrue([for name, volume in local.compose.volumes : volume.driver_opts.device == "/srv/ark-mcp/${name}"])
    error_message = "Every Compose data volume must be on the retained disk."
  }
  assert {
    condition     = !contains(keys(local.compose.services["ark-mcp"]), "ports") && !contains(keys(local.compose.services.redis), "ports")
    error_message = "Only Caddy may publish network ports."
  }
}

run "existing_network_and_private_bucket" {
  command = plan
  variables {
    create_vpc         = false
    existing_vpc_id    = "vpc-existing"
    existing_subnet_id = "subnet-existing"
    admin_cidrs        = ["203.0.113.10/32"]
    tos_bucket_name    = "ark-mcp-test-media"
  }
  assert {
    condition     = length(bytepluscc_vpc_vpc.this) == 0 && length(bytepluscc_vpc_subnet.this) == 0 && output.subnet_id == "subnet-existing"
    error_message = "Existing-network mode must not create or replace the shared network."
  }
  assert {
    condition     = bytepluscc_tos_bucket.media[0].acl_grant.acl == "private"
    error_message = "Optional media storage must not be public."
  }
  assert {
    condition     = alltrue([for rule in bytepluscc_vpc_security_group.this.ingress_permissions : rule.port_start != 22 || rule.cidr_ip == "203.0.113.10/32"])
    error_message = "SSH must be limited to the configured administrator network."
  }
}

run "reject_public_ssh" {
  command = plan
  variables { admin_cidrs = ["0.0.0.0/0"] }
  expect_failures = [var.admin_cidrs]
}

run "reject_unpinned_image" {
  command = plan
  variables { ark_mcp_image = "ghcr.io/byteplus-sa/ark-mcp:latest" }
  expect_failures = [var.ark_mcp_image]
}

run "reject_unconfigured_example_image" {
  command = plan
  variables {
    ark_mcp_image = regex("(?m)^ark_mcp_image\\s*=\\s*\"([^\"]+)\"", file("terraform.tfvars.example"))[0]
  }
  expect_failures = [var.ark_mcp_image]
}

run "reject_hostname_injection" {
  command = plan
  variables { public_hostname = "mcp.team.test\nMCP_AUTH_MODE=local" }
  expect_failures = [var.public_hostname]
}

run "reject_missing_existing_network" {
  command = plan
  variables { create_vpc = false }
  expect_failures = [var.existing_vpc_id, var.existing_subnet_id]
}

run "reject_wrong_existing_zone" {
  command = plan
  variables {
    create_vpc         = false
    existing_vpc_id    = "vpc-existing"
    existing_subnet_id = "subnet-existing"
    zone_id            = "ap-southeast-1b"
  }
  expect_failures = [bytepluscc_ecs_instance.this]
}

run "reject_wrong_existing_vpc" {
  command = plan
  variables {
    create_vpc         = false
    existing_vpc_id    = "vpc-other"
    existing_subnet_id = "subnet-existing"
  }
  expect_failures = [bytepluscc_ecs_instance.this]
}

run "reject_fractional_disk" {
  command = plan
  variables { data_disk_size_gib = 40.5 }
  expect_failures = [var.data_disk_size_gib]
}

run "reject_ipv6_vpc" {
  command = plan
  variables { vpc_cidr = "2001:db8::/32" }
  expect_failures = [var.vpc_cidr]
}
