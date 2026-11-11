locals {
  tags = [
    { key = "application", value = "ark-mcp" },
    { key = "deployment", value = var.name },
  ]
  vpc_id    = var.create_vpc ? bytepluscc_vpc_vpc.this[0].vpc_id : var.existing_vpc_id
  subnet_id = var.create_vpc ? bytepluscc_vpc_subnet.this[0].subnet_id : var.existing_subnet_id

  ingress_rules = concat(
    [{ port = 80, protocol = "tcp", cidr = "0.0.0.0/0", description = "ACME and HTTPS redirect" }],
    flatten([
      for cidr in sort(tolist(var.https_cidrs)) : [
        { port = 443, protocol = "tcp", cidr = cidr, description = "HTTPS MCP" },
        { port = 443, protocol = "udp", cidr = cidr, description = "HTTP3" },
      ]
    ]),
    [for cidr in sort(tolist(var.admin_cidrs)) : {
      port = 22, protocol = "tcp", cidr = cidr, description = "Restricted SSH administration"
    }]
  )
  egress_rules = [
    { port = 80, protocol = "tcp", description = "OS package updates" },
    { port = 443, protocol = "tcp", description = "Providers registry and identity endpoints" },
    { port = 53, protocol = "tcp", description = "DNS over TCP" },
    { port = 53, protocol = "udp", description = "DNS over UDP" },
    { port = 123, protocol = "udp", description = "Clock synchronization" },
  ]
}

resource "bytepluscc_vpc_vpc" "this" {
  count        = var.create_vpc ? 1 : 0
  vpc_name     = "${var.name}-vpc"
  cidr_block   = var.vpc_cidr
  project_name = var.project_name
  tags         = local.tags
}

resource "bytepluscc_vpc_subnet" "this" {
  count       = var.create_vpc ? 1 : 0
  subnet_name = "${var.name}-subnet"
  vpc_id      = local.vpc_id
  zone_id     = var.zone_id
  cidr_block  = cidrsubnet(var.vpc_cidr, 24 - tonumber(split("/", var.vpc_cidr)[1]), 0)
  tags        = local.tags
}

data "bytepluscc_vpc_subnet" "existing" {
  count = var.create_vpc ? 0 : 1
  id    = var.existing_subnet_id
}

resource "bytepluscc_vpc_security_group" "this" {
  vpc_id              = local.vpc_id
  security_group_name = "${var.name}-ingress"
  description         = "Ark MCP HTTPS and restricted administration"
  project_name        = var.project_name
  tags                = local.tags

  ingress_permissions = [for rule in local.ingress_rules : {
    direction       = "ingress"
    policy          = "accept"
    priority        = 10
    protocol        = rule.protocol
    port_start      = rule.port
    port_end        = rule.port
    cidr_ip         = rule.cidr
    description     = rule.description
    prefix_list_id  = ""
    source_group_id = ""
  }]
  egress_permissions = [for rule in local.egress_rules : {
    direction       = "egress"
    policy          = "accept"
    priority        = 10
    protocol        = rule.protocol
    port_start      = rule.port
    port_end        = rule.port
    cidr_ip         = "0.0.0.0/0"
    description     = rule.description
    prefix_list_id  = ""
    source_group_id = ""
  }]
}

resource "terraform_data" "bootstrap_revision" {
  triggers_replace = sha256(local.cloud_init)
}

resource "bytepluscc_ecs_instance" "this" {
  instance_name        = var.name
  hostname             = var.name
  instance_type        = var.instance_type
  instance_charge_type = "PostPaid"
  spot_strategy        = "NoSpot"
  zone_id              = var.zone_id
  project_name         = var.project_name
  deletion_protection  = var.instance_deletion_protection
  tags                 = local.tags
  image                = { image_id = var.image_id }
  key_pair             = { key_pair_name = var.key_pair_name }
  primary_network_interface = {
    subnet_id          = local.subnet_id
    security_group_ids = [bytepluscc_vpc_security_group.this.security_group_id]
  }
  system_volume = {
    size                 = 40
    volume_type          = var.volume_type
    delete_with_instance = true
  }
  user_data = base64encode(local.cloud_init)

  lifecycle {
    replace_triggered_by = [terraform_data.bootstrap_revision]

    precondition {
      condition = var.create_vpc ? true : (
        data.bytepluscc_vpc_subnet.existing[0].vpc_id == var.existing_vpc_id &&
        data.bytepluscc_vpc_subnet.existing[0].zone_id == var.zone_id
      )
      error_message = "The existing subnet must belong to the specified VPC and availability zone."
    }
    precondition {
      condition     = length(local.cloud_init) <= 16384
      error_message = "Decoded ECS user data exceeds the 16 KiB limit."
    }
  }
}

resource "bytepluscc_storageebs_volume" "state" {
  volume_name          = "${var.name}-state"
  volume_type          = var.volume_type
  zone_id              = var.zone_id
  size                 = var.data_disk_size_gib
  kind                 = "data"
  pay_type             = "post"
  instance_id          = bytepluscc_ecs_instance.this.instance_id
  delete_with_instance = false
  project_name         = var.project_name
  tags                 = local.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "bytepluscc_vpc_eip" "this" {
  name                  = "${var.name}-eip"
  billing_type          = 3
  bandwidth             = var.eip_bandwidth_mbps
  isp                   = "BGP"
  instance_id           = bytepluscc_ecs_instance.this.instance_id
  instance_type         = "EcsInstance"
  release_with_instance = false
  project_name          = var.project_name
  tags                  = local.tags
}

resource "bytepluscc_tos_bucket" "media" {
  count         = var.tos_bucket_name == null ? 0 : 1
  name          = var.tos_bucket_name
  project_name  = var.project_name
  storage_class = "STANDARD"
  acl_grant = {
    acl                = "private"
    grant_full_control = ""
    grant_read         = ""
    grant_read_acp     = ""
    grant_write        = ""
    grant_write_acp    = ""
  }
  tags = local.tags

  lifecycle {
    prevent_destroy = true
  }
}
