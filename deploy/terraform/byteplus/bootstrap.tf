locals {
  base_compose = yamldecode(file("${path.module}/../../compose/compose.yaml"))
  compose = merge(local.base_compose, {
    services = {
      for name, service in local.base_compose.services : name => merge(
        { for key, value in service : key => value if key != "build" },
        name == "ark-mcp" ? {
          image    = var.ark_mcp_image
          env_file = "/srv/ark-mcp/config/.env"
        } : {}
      )
    }
    volumes = {
      for name, volume in local.base_compose.volumes : name => {
        driver = "local"
        driver_opts = {
          type   = "none"
          o      = "bind"
          device = "/srv/ark-mcp/${name}"
        }
      }
    }
  })
  environment_example = join("\n", concat(
    [for line in split("\n", file("${path.module}/../../compose/.env.example")) :
      startswith(line, "MCP_PUBLIC_HOST=") ? "MCP_PUBLIC_HOST=${var.public_hostname}" : line
      if can(regex("^[A-Z][A-Z0-9_]*=", line))
    ],
    var.tos_bucket_name == null ? [] : [
      "OBJECT_STORAGE_BACKEND=tos",
      "TOS_BUCKET=${var.tos_bucket_name}",
      "TOS_REGION=${var.region}",
      "TOS_ACCESS_KEY=",
      "TOS_SECRET_KEY=",
    ],
    [""]
  ))
  bootstrap_files = {
    "/opt/ark-mcp/compose.yaml" = {
      content = yamlencode(local.compose), permissions = "0644"
    }
    "/opt/ark-mcp/Caddyfile" = {
      content = file("${path.module}/../../compose/Caddyfile"), permissions = "0644"
    }
    "/opt/ark-mcp/.env.example" = {
      content = local.environment_example, permissions = "0600"
    }
    "/opt/ark-mcp/activate.sh" = {
      content = file("${path.module}/scripts/activate.sh"), permissions = "0700"
    }
    "/opt/ark-mcp/check_env.py" = {
      content = file("${path.module}/scripts/check_env.py"), permissions = "0644"
    }
    "/opt/ark-mcp/generate_mcp_key.py" = {
      content = file("${path.module}/../../../scripts/generate_mcp_key.py"), permissions = "0700"
    }
    "/opt/ark-mcp/install.sh" = {
      content = file("${path.module}/scripts/install.sh"), permissions = "0700"
    }
    "/opt/ark-mcp/docker-storage.conf" = {
      content = file("${path.module}/scripts/docker-storage.conf"), permissions = "0644"
    }
    "/etc/systemd/system/ark-mcp.service" = {
      content = file("${path.module}/scripts/ark-mcp.service"), permissions = "0644"
    }
    "/etc/systemd/system/ark-mcp-install.service" = {
      content = file("${path.module}/scripts/ark-mcp-install.service"), permissions = "0644"
    }
  }
  cloud_init = "#cloud-config\n${yamlencode({
    write_files = [for path, entry in local.bootstrap_files : {
      path        = path
      owner       = "root:root"
      permissions = entry.permissions
      encoding    = "gz+b64"
      content     = base64gzip(entry.content)
    }]
    runcmd = [
      ["systemctl", "daemon-reload"],
      ["systemctl", "enable", "--now", "--no-block", "ark-mcp-install.service"],
    ]
  })}"
}
