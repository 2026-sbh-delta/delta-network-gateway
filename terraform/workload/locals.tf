locals {
  prefix = "${var.name}-${var.environment}-client"
  common_tags = merge({
    Team        = "Delta"
    Project     = "delta-multicloud"
    Environment = var.environment
    ManagedBy   = "Terraform"
  }, var.tags)
  subnets = merge([for az_id, az in var.azs : {
    "${az_id}/tgw"    = { az_id = az_id, role = "tgw", cidr = az.tgw_subnet_cidr }
    "${az_id}/client" = { az_id = az_id, role = "client", cidr = az.client_subnet_cidr }
  }]...)
  networks = concat(
    [{ key = "workload", cidr = var.vpc_cidr }],
    [for cidr in var.inspection_cidrs : { key = "inspection/${cidr}", cidr = cidr }],
    [for cidr in var.remote_cidrs : { key = "remote/${cidr}", cidr = cidr }]
  )
  network_ranges = [for network in local.networks : {
    key   = network.key
    start = try(sum([for i, octet in split(".", cidrhost(network.cidr, 0)) : tonumber(octet) * pow(256, 3 - i)]), -1)
    end   = try(sum([for i, octet in split(".", cidrhost(network.cidr, -1)) : tonumber(octet) * pow(256, 3 - i)]), -1)
  }]
  vpc_range = one([for network in local.network_ranges : network if network.key == "workload"])
  subnet_ranges = [for key, subnet in local.subnets : {
    key   = key
    start = try(sum([for i, octet in split(".", cidrhost(subnet.cidr, 0)) : tonumber(octet) * pow(256, 3 - i)]), -1)
    end   = try(sum([for i, octet in split(".", cidrhost(subnet.cidr, -1)) : tonumber(octet) * pow(256, 3 - i)]), -1)
  }]
  private_ranges = [for cidr in ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"] : {
    start = sum([for i, octet in split(".", cidrhost(cidr, 0)) : tonumber(octet) * pow(256, 3 - i)])
    end   = sum([for i, octet in split(".", cidrhost(cidr, -1)) : tonumber(octet) * pow(256, 3 - i)])
  }]
  routes = { for item in flatten([for az_id in keys(var.azs) : [for cidr in var.remote_cidrs : {
    key = "${az_id}/${cidr}", az_id = az_id, cidr = cidr
  }]]) : item.key => item }
  egress = { for item in flatten([for cidr in var.remote_cidrs : [for port in var.test_tcp_ports : {
    key = "${cidr}/${port}", cidr = cidr, port = port
  }]]) : item.key => item }
}

resource "terraform_data" "contract" {
  input = "delta-private-workload-v1"
  lifecycle {
    precondition {
      condition     = data.aws_caller_identity.current.account_id == var.workload_account_id
      error_message = "Authenticated AWS account differs from workload_account_id."
    }
    precondition {
      condition = alltrue([for id, az in data.aws_availability_zone.selected :
        id == az.zone_id && az.region == var.region && az.zone_type == "availability-zone"
      ])
      error_message = "Every physical AZ ID must be an available standard AZ in region."
    }
    precondition {
      condition = alltrue([for network in local.network_ranges : anytrue([for private in local.private_ranges :
        network.start >= private.start && network.end <= private.end
      ])])
      error_message = "Workload, inspection and remote networks must be entirely within RFC1918 private IPv4 ranges; no public or default routes."
    }
    precondition {
      condition = alltrue(flatten([for i, first in local.network_ranges : [for j, second in local.network_ranges :
        !(first.start <= second.end && second.start <= first.end) if i < j
      ]]))
      error_message = "Workload, inspection and remote networks must not overlap, including contained prefixes."
    }
    precondition {
      condition = alltrue([for subnet in local.subnet_ranges : subnet.start >= local.vpc_range.start && subnet.end <= local.vpc_range.end]) && alltrue(flatten([
        for i, first in local.subnet_ranges : [for j, second in local.subnet_ranges : !(first.start <= second.end && second.start <= first.end) if i < j]
      ]))
      error_message = "All four dedicated subnets must be disjoint and contained in the workload VPC."
    }
  }
}
