locals {
  internet_inspection_prepared = var.internet_inspection != null
  internet_inspection_active   = try(var.internet_inspection.mode == "enabled", false)
  internet_firewall_subnets    = try(var.internet_inspection.subnet_cidrs, {})
  internet_existing_cidrs = concat(
    [for subnet in local.subnets : subnet.cidr],
    values(var.application.public_subnet_cidrs), values(var.application.db_subnet_cidrs)
  )
  internet_subnet_ranges = [for cidr in concat(local.internet_existing_cidrs, values(local.internet_firewall_subnets)) : {
    start = try(sum([for index, octet in split(".", cidrhost(cidr, 0)) : tonumber(octet) * pow(256, 3 - index)]), -1)
    end   = try(sum([for index, octet in split(".", cidrhost(cidr, -1)) : tonumber(octet) * pow(256, 3 - index)]), -1)
  }]
}

resource "terraform_data" "internet_inspection_contract" {
  count = local.internet_inspection_prepared ? 1 : 0
  input = "delta-board-internet-inspection-v1"
  lifecycle {
    precondition {
      condition = var.application_enabled && (
        var.internet_inspection.expected_vpc_id == aws_vpc.client.id &&
        split(":", var.internet_inspection.firewall_arn)[3] == var.region &&
        split(":", var.internet_inspection.firewall_arn)[4] == var.central_account_id
      )
      error_message = "Internet inspection must match this existing application VPC and the central firewall account/Region."
    }
    precondition {
      condition = (
        toset(keys(local.internet_firewall_subnets)) == toset(keys(var.azs)) &&
        length(setsubtract(toset(keys(var.azs)), var.internet_inspection.primary_az_ids)) == 0 &&
        alltrue([for cidr in values(local.internet_firewall_subnets) : try(
          cidrnetmask(cidr) != "" && split("/", cidr)[1] == "24" &&
          cidrhost(cidr, 0) == split("/", cidr)[0] && startswith(cidr, "${join(".", slice(split(".", var.vpc_cidr), 0, 2))}."), false
          )]) && alltrue(flatten([for i, first in local.internet_subnet_ranges : [
            for j, second in local.internet_subnet_ranges : !(first.start <= second.end && second.start <= first.end) if i < j
        ]]))
      )
      error_message = "Firewall /24 subnets must cover both primary physical AZs, be inside this /16, and not overlap existing subnets."
    }
  }
}

resource "aws_subnet" "internet_firewall" {
  for_each                = local.internet_firewall_subnets
  vpc_id                  = aws_vpc.client.id
  availability_zone_id    = each.key
  cidr_block              = each.value
  map_public_ip_on_launch = false
  tags                    = { Name = "${local.prefix}-${each.key}-internet-firewall" }
  depends_on              = [terraform_data.internet_inspection_contract]
}

resource "aws_networkfirewall_vpc_endpoint_association" "internet" {
  for_each     = local.internet_firewall_subnets
  firewall_arn = var.internet_inspection.firewall_arn
  vpc_id       = aws_vpc.client.id
  subnet_mapping {
    subnet_id       = aws_subnet.internet_firewall[each.key].id
    ip_address_type = "IPV4"
  }
  tags = { Name = "${local.prefix}-${each.key}-internet-firewall" }
}

locals {
  internet_endpoint_ids = { for az_id, subnet in aws_subnet.internet_firewall : az_id => one(flatten([
    for sync in one(aws_networkfirewall_vpc_endpoint_association.internet[az_id].vpc_endpoint_association_status).association_sync_state : [
      for attachment in sync.attachment : attachment.endpoint_id
      if attachment.subnet_id == subnet.id && attachment.status == "READY"
    ] if sync.availability_zone == data.aws_availability_zone.selected[az_id].name
  ])) }
}

resource "aws_route_table" "internet_firewall" {
  for_each = local.internet_firewall_subnets
  vpc_id   = aws_vpc.client.id
  tags     = { Name = "${local.prefix}-${each.key}-internet-firewall" }
}

resource "aws_route_table_association" "internet_firewall" {
  for_each       = local.internet_firewall_subnets
  subnet_id      = aws_subnet.internet_firewall[each.key].id
  route_table_id = aws_route_table.internet_firewall[each.key].id
}

resource "aws_route" "internet_firewall_egress" {
  for_each               = local.internet_firewall_subnets
  route_table_id         = aws_route_table.internet_firewall[each.key].id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = module.sample_app[0].inspection_network.internet_gateway_id
}

resource "aws_route_table" "internet_ingress" {
  count  = local.internet_inspection_prepared ? 1 : 0
  vpc_id = aws_vpc.client.id
  tags   = { Name = "${local.prefix}-internet-ingress" }
}

resource "aws_route" "internet_ingress" {
  for_each               = local.internet_firewall_subnets
  route_table_id         = aws_route_table.internet_ingress[0].id
  destination_cidr_block = var.application.public_subnet_cidrs[each.key]
  vpc_endpoint_id        = local.internet_endpoint_ids[each.key]
  lifecycle {
    precondition {
      condition     = local.internet_endpoint_ids[each.key] != null
      error_message = "The secondary firewall endpoint must be uniquely READY in this physical AZ."
    }
  }
}

resource "aws_route_table_association" "internet_ingress" {
  count          = local.internet_inspection_active ? 1 : 0
  gateway_id     = module.sample_app[0].inspection_network.internet_gateway_id
  route_table_id = aws_route_table.internet_ingress[0].id
  depends_on     = [aws_route.internet_ingress, aws_route.internet_firewall_egress]
}

output "internet_inspection" {
  description = "Shared firewall scope, physical AZ endpoints and prepared/active edge route table."
  value = local.internet_inspection_prepared ? {
    mode                   = var.internet_inspection.mode
    vpc_id                 = aws_vpc.client.id
    firewall_arn           = var.internet_inspection.firewall_arn
    endpoint_ids_by_az_id  = local.internet_endpoint_ids
    firewall_subnet_ids    = { for az, subnet in aws_subnet.internet_firewall : az => subnet.id }
    firewall_route_tables  = { for az, table in aws_route_table.internet_firewall : az => table.id }
    az_names               = { for az, data in data.aws_availability_zone.selected : az => data.name }
    association_arns       = { for az, association in aws_networkfirewall_vpc_endpoint_association.internet : az => association.vpc_endpoint_association_arn }
    ingress_route_table_id = aws_route_table.internet_ingress[0].id
    internet_gateway_id    = module.sample_app[0].inspection_network.internet_gateway_id
    public_subnets         = module.sample_app[0].inspection_network.public_subnets
  } : null
}
