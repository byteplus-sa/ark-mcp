variable "region" {
  type        = string
  description = "BytePlus infrastructure region. Does not change the ModelArk API region."
  default     = "ap-southeast-1"
  nullable    = false
}

variable "zone_id" {
  type        = string
  description = "Availability zone containing the ECS instance, subnet, and retained EBS disk."
  nullable    = false

  validation {
    condition     = startswith(var.zone_id, "${var.region}")
    error_message = "zone_id must belong to the configured region."
  }
}

variable "name" {
  type        = string
  description = "Unique deployment name for this team or organization."
  default     = "ark-mcp"
  nullable    = false

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,38}[a-z0-9]$", var.name))
    error_message = "Use 3-40 lowercase letters, digits, and hyphens, starting with a letter and ending with a letter or digit."
  }
}

variable "project_name" {
  type        = string
  description = "Existing BytePlus project used to group deployment resources."
  default     = "default"
  nullable    = false
}

variable "image_id" {
  type        = string
  description = "Regional Ubuntu 24.04 AMD64 ECS image with cloud-init. Verify its publisher and architecture before applying."
  nullable    = false

  validation {
    condition     = can(regex("^image-[A-Za-z0-9-]+$", var.image_id))
    error_message = "Provide a regional Ubuntu 24.04 AMD64 image ID."
  }
}

variable "instance_type" {
  type        = string
  description = "Available AMD64 ECS instance type in this zone. Start with at least 2 vCPU and 4 GiB RAM."
  nullable    = false

  validation {
    condition     = can(regex("^ecs\\.[a-zA-Z0-9.]+$", var.instance_type))
    error_message = "Provide an ECS instance type available in the selected zone."
  }
}

variable "key_pair_name" {
  type        = string
  description = "Existing regional ECS SSH key pair name. Terraform never creates or accepts a private key."
  nullable    = false

  validation {
    condition     = length(trimspace(var.key_pair_name)) > 0
    error_message = "An existing SSH key pair name is required."
  }
}

variable "create_vpc" {
  type        = bool
  description = "Create a dedicated VPC and subnet. Set false to use existing_vpc_id and existing_subnet_id."
  default     = true
  nullable    = false
}

variable "existing_vpc_id" {
  type        = string
  description = "Existing VPC ID, required only when create_vpc=false."
  default     = null

  validation {
    condition     = var.create_vpc ? var.existing_vpc_id == null : can(regex("^vpc-[A-Za-z0-9-]+$", var.existing_vpc_id))
    error_message = "Set existing_vpc_id only when create_vpc=false; it is required in that mode."
  }
}

variable "existing_subnet_id" {
  type        = string
  description = "Existing subnet ID in the specified VPC and zone, required only when create_vpc=false."
  default     = null

  validation {
    condition     = var.create_vpc ? var.existing_subnet_id == null : can(regex("^subnet-[A-Za-z0-9-]+$", var.existing_subnet_id))
    error_message = "Set existing_subnet_id only when create_vpc=false; it is required in that mode."
  }
}

variable "vpc_cidr" {
  type        = string
  description = "Private IPv4 /16 to /24 block for a new VPC. Its first /24 becomes the subnet."
  default     = "10.42.0.0/16"
  nullable    = false

  validation {
    condition = can(cidrnetmask(var.vpc_cidr)) && can(regex(
      "^(10\\.|192\\.168\\.|172\\.(1[6-9]|2[0-9]|3[01])\\.).*/(1[6-9]|2[0-4])$", var.vpc_cidr
    ))
    error_message = "Use an RFC1918 IPv4 CIDR with a /16 through /24 prefix."
  }
}

variable "admin_cidrs" {
  type        = set(string)
  description = "IPv4 /24 or narrower networks allowed SSH. Empty disables network SSH; use the ECS console."
  default     = []
  nullable    = false

  validation {
    condition = alltrue([
      for cidr in var.admin_cidrs :
      can(cidrnetmask(cidr)) && can(regex("/(2[4-9]|3[0-2])$", cidr))
    ])
    error_message = "SSH sources must be valid IPv4 /24 or narrower CIDRs; never 0.0.0.0/0."
  }
}

variable "https_cidrs" {
  type        = set(string)
  description = "IPv4 networks allowed HTTPS on TCP/UDP 443. TCP 80 remains public for ACME certificates."
  default     = ["0.0.0.0/0"]
  nullable    = false

  validation {
    condition     = length(var.https_cidrs) > 0 && alltrue([for cidr in var.https_cidrs : can(cidrnetmask(cidr))])
    error_message = "Provide at least one valid IPv4 CIDR for HTTPS clients."
  }
}

variable "public_hostname" {
  type        = string
  description = "Public DNS hostname for HTTPS. Null selects an EIP-derived sslip.io name; install MCP_PUBLIC_HOST from the public_hostname output after apply."
  default     = null

  validation {
    condition     = var.public_hostname == null ? true : length(var.public_hostname) <= 253 && can(regex("^([a-z0-9]([a-z0-9-]*[a-z0-9])?\\.)+[a-z]{2,63}$", var.public_hostname))
    error_message = "Use null or a lowercase DNS hostname, without scheme, port, wildcard, or path."
  }
}

variable "ark_mcp_image" {
  type        = string
  description = "Publicly pullable Linux AMD64 image verified to support shared-key authentication, pinned to a sha256 digest or numeric release tag. Never latest or edge."
  nullable    = false

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9./:_-]*(@sha256:[a-f0-9]{64}|:v?[0-9]+\\.[0-9]+\\.[0-9]+)$", var.ark_mcp_image))
    error_message = "Replace the example placeholder with a shared-key-capable image pinned to a sha256 digest or numeric release tag; verify compatibility before applying."
  }
}

variable "data_disk_size_gib" {
  type        = number
  description = "Retained EBS data disk capacity in GiB. Expanding it also requires an operator filesystem resize."
  default     = 100
  nullable    = false

  validation {
    condition     = var.data_disk_size_gib >= 40 && var.data_disk_size_gib <= 65536 && floor(var.data_disk_size_gib) == var.data_disk_size_gib
    error_message = "Use an integer data disk size from 40 through 65536 GiB."
  }
}

variable "volume_type" {
  type        = string
  description = "EBS SSD type available in the selected zone."
  default     = "ESSD_PL0"
  nullable    = false

  validation {
    condition     = contains(["ESSD_PL0", "ESSD_FlexPL"], var.volume_type)
    error_message = "Use ESSD_PL0 or ESSD_FlexPL."
  }
}

variable "instance_deletion_protection" {
  type        = bool
  description = "Block ECS deletion, including replacement. Explicitly disable and apply before a planned replacement."
  default     = true
  nullable    = false
}

variable "eip_bandwidth_mbps" {
  type        = number
  description = "Traffic-billed EIP bandwidth cap in Mbps. EIP, traffic, compute, and storage incur BytePlus charges."
  default     = 20
  nullable    = false

  validation {
    condition     = var.eip_bandwidth_mbps >= 1 && var.eip_bandwidth_mbps <= 200 && floor(var.eip_bandwidth_mbps) == var.eip_bandwidth_mbps
    error_message = "Use an integer bandwidth cap from 1 through 200 Mbps."
  }
}

variable "tos_bucket_name" {
  type        = string
  description = "Optional globally unique private media bucket name. Does not configure application credentials or change its artifact backend."
  default     = null

  validation {
    condition     = var.tos_bucket_name == null ? true : can(regex("^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$", var.tos_bucket_name))
    error_message = "Use a 3-63 character lowercase bucket name containing only letters, digits, and hyphens."
  }
}
