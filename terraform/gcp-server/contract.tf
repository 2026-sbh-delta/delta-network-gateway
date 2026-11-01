locals {
  labels     = { project = "delta", environment = "lab", managed_by = "terraform" }
  server_tag = "${var.name}-private-server"
  all_cidrs  = setunion(var.connected_cidrs, var.aws_client_cidrs, toset([var.address_space]))
  cidr_ranges = { for cidr in local.all_cidrs : cidr => {
    first = try(sum([for i, octet in split(".", cidrhost(cidr, 0)) : tonumber(octet) * pow(256, 3 - i)]), -1)
    last  = try(sum([for i, octet in split(".", cidrhost(cidr, -1)) : tonumber(octet) * pow(256, 3 - i)]), -1)
  } }
  # Numeric IPv4 intervals also reject non-public/documentation CGW endpoints.
  non_public        = ["0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24", "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24", "224.0.0.0/3"]
  tunnel_public_ips = { for k, t in var.vpn_tunnels : k => try(sum([for i, octet in split(".", cidrhost("${t.aws_outside_ip}/32", 0)) : tonumber(octet) * pow(256, 3 - i)]), -1) }
}

resource "terraform_data" "contract" {
  input = { project_id = var.project_id, project_number = var.project_number }
  lifecycle {
    precondition {
      condition     = data.google_project.current.project_id == var.project_id && tostring(data.google_project.current.number) == var.project_number
      error_message = "The authenticated project does not match the authorized project ID/number."
    }
    precondition {
      condition     = alltrue([for cidr in var.connected_cidrs : local.cidr_ranges[cidr].last < local.cidr_ranges[var.address_space].first || local.cidr_ranges[cidr].first > local.cidr_ranges[var.address_space].last])
      error_message = "GCP address_space overlaps a connected network."
    }
    precondition {
      condition     = alltrue([for source in var.aws_client_cidrs : anytrue([for cidr in var.connected_cidrs : local.cidr_ranges[source].first >= local.cidr_ranges[cidr].first && local.cidr_ranges[source].last <= local.cidr_ranges[cidr].last])])
      error_message = "Every AWS source must be contained in a registered connected network."
    }
    precondition {
      condition = alltrue([for k, ip in local.tunnel_public_ips : ip >= 0 && alltrue([for cidr in local.non_public : !try(
        ip >= sum([for i, octet in split(".", cidrhost(cidr, 0)) : tonumber(octet) * pow(256, 3 - i)]) && ip <= sum([for i, octet in split(".", cidrhost(cidr, -1)) : tonumber(octet) * pow(256, 3 - i)]), true
      )])])
      error_message = "AWS outside endpoints must be real public IPv4 addresses."
    }
  }
}
