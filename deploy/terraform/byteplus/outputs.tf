output "mcp_url" {
  description = "Intended HTTPS endpoint; usable only after DNS, persistent storage, credentials, and activation are complete."
  value       = "https://${var.public_hostname}/mcp"
}

output "public_ip" {
  description = "Create an A record for public_hostname pointing at this retained EIP."
  value       = bytepluscc_vpc_eip.this.eip_address
}

output "instance_id" {
  description = "ECS instance to inspect in the BytePlus console."
  value       = bytepluscc_ecs_instance.this.instance_id
}

output "vpc_id" {
  description = "Created or reused VPC ID."
  value       = local.vpc_id
}

output "subnet_id" {
  description = "Created or reused subnet ID."
  value       = local.subnet_id
}

output "state_volume" {
  description = "Retained EBS disk. Confirm the device inside the guest before initializing; never format during replacement."
  value = {
    id          = bytepluscc_storageebs_volume.state.volume_id
    device_name = bytepluscc_storageebs_volume.state.device_name
    mount_path  = "/srv/ark-mcp"
    zone_id     = var.zone_id
  }
}

output "tos_bucket" {
  description = "Optional private bucket; application credentials, lifecycle policy, and artifact backend remain operator configuration."
  value       = var.tos_bucket_name == null ? null : bytepluscc_tos_bucket.media[0].name
}

output "next_steps" {
  description = "Terraform success is infrastructure success, not application readiness."
  value = [
    "Create the public hostname A record pointing to public_ip.",
    "Check cloud-init status and ark-mcp-install.service on the VM.",
    "Verify and mount the retained disk at /srv/ark-mcp using its filesystem UUID in /etc/fstab.",
    "Install a root-owned mode-0600 /srv/ark-mcp/config/.env using /opt/ark-mcp/.env.example.",
    "Run sudo /opt/ark-mcp/activate.sh, then check HTTPS /health and an unauthenticated /mcp request.",
  ]
}
