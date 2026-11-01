locals {
  prefix = "${var.name}-${var.environment}-cloudfront-server"
  common_tags = merge({
    Team = "Delta", Project = "delta-multicloud", Environment = var.environment, ManagedBy = "Terraform"
  }, var.tags)
  alb_name = "${var.name}-${var.environment}-anf-demo"
  alb_ranges = [for key, cidr in var.alb_subnets : {
    key   = key
    start = try(sum([for i, octet in split(".", cidrhost(cidr, 0)) : tonumber(octet) * pow(256, 3 - i)]), -1)
    end   = try(sum([for i, octet in split(".", cidrhost(cidr, -1)) : tonumber(octet) * pow(256, 3 - i)]), -1)
  }]
  ingress_range = {
    start = sum([for i, octet in split(".", cidrhost(data.aws_vpc.ingress.cidr_block, 0)) : tonumber(octet) * pow(256, 3 - i)])
    end   = sum([for i, octet in split(".", cidrhost(data.aws_vpc.ingress.cidr_block, -1)) : tonumber(octet) * pow(256, 3 - i)])
  }
  test_ports = { allow = 8080, deny = 8081 }
  server_alb_rules = { for rule in flatten([for az, cidr in var.alb_subnets : [for port in values(local.test_ports) : {
    key = "${az}/${port}", cidr = cidr, port = port
  }]]) : rule.key => rule }
}
resource "terraform_data" "contract" {
  input = "delta-inspected-alb-demo-v2"
  lifecycle {
    precondition {
      condition     = data.aws_caller_identity.current.account_id == var.workload_account_id && data.aws_vpc.server.owner_id == var.workload_account_id && split(":", data.aws_instance.server.arn)[4] == var.workload_account_id
      error_message = "STS, Server VPC and EC2 must belong to the authorized Workload account."
    }
    precondition {
      condition     = data.aws_subnet.server.vpc_id == var.vpc_id && data.aws_security_group.server.vpc_id == var.vpc_id && contains(data.aws_instance.server.vpc_security_group_ids, var.server_security_group_id)
      error_message = "The existing EC2 subnet and attached server SG must be in vpc_id."
    }
    precondition {
      condition     = data.aws_instance.server.instance_state == "running" && data.aws_instance.server.private_ip == var.expected_server_private_ip && length(data.aws_instance.server.private_dns) > 0
      error_message = "The origin must be running at the expected private IP with a private DNS name."
    }
    precondition {
      condition     = (data.aws_instance.server.public_ip == null || data.aws_instance.server.public_ip == "") && length(data.aws_instance.server.ipv6_addresses) == 0
      error_message = "The origin must have no public IPv4 or IPv6 addresses."
    }
    precondition {
      condition     = data.aws_vpc.server.enable_dns_support && data.aws_vpc.server.enable_dns_hostnames && !data.aws_subnet.server.map_public_ip_on_launch
      error_message = "The existing VPC must support private DNS and the Server subnet must disable automatic public IPv4 assignment."
    }
    precondition {
      condition     = data.aws_vpc.ingress.owner_id == var.workload_account_id && data.aws_vpc.ingress.enable_dns_support && data.aws_vpc.ingress.enable_dns_hostnames && length(local.alb_name) <= 26
      error_message = "The ingress VPC must be owned by Workload with private DNS enabled; the ALB and target group names must fit 32 characters."
    }
    precondition {
      condition = alltrue([for subnet in local.alb_ranges : subnet.start >= local.ingress_range.start && subnet.end <= local.ingress_range.end]) && alltrue(flatten([
        for i, first in local.alb_ranges : [for j, second in local.alb_ranges : !(first.start <= second.end && second.start <= first.end) if i < j]
      ]))
      error_message = "ALB /24 subnets must be disjoint and contained in the existing ingress VPC."
    }
    precondition {
      condition     = alltrue([for id, az in data.aws_availability_zone.alb : az.zone_id == id && az.region == var.region && az.zone_type == "availability-zone"])
      error_message = "ALB AZ IDs must be available standard zones in the configured Region."
    }
    precondition {
      condition     = data.aws_ec2_transit_gateway.central.owner_id == var.central_account_id && data.aws_ec2_transit_gateway.central.default_route_table_association == "disable" && data.aws_ec2_transit_gateway.central.default_route_table_propagation == "disable"
      error_message = "Use the authorized central TGW with default association and propagation disabled."
    }
  }
}
