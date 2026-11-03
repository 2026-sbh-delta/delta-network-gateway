resource "aws_vpc" "inspection" {
  cidr_block           = var.inspection_vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${local.prefix}-inspection" }
  depends_on           = [terraform_data.contract]
}

resource "aws_subnet" "inspection" {
  for_each                = local.subnets
  vpc_id                  = aws_vpc.inspection.id
  availability_zone_id    = each.value.az_id
  cidr_block              = each.value.cidr
  map_public_ip_on_launch = false
  tags                    = { Name = "${local.prefix}-${each.value.role}-${each.value.az_id}" }
}

resource "aws_route_table" "tgw" {
  for_each = var.inspection_azs
  vpc_id   = aws_vpc.inspection.id
  tags     = { Name = "${local.prefix}-tgw-${each.key}" }
}

resource "aws_route_table" "firewall" {
  for_each = var.inspection_azs
  vpc_id   = aws_vpc.inspection.id
  tags     = { Name = "${local.prefix}-firewall-${each.key}" }
}

resource "aws_route_table_association" "tgw" {
  for_each       = var.inspection_azs
  subnet_id      = aws_subnet.inspection["${each.key}/tgw"].id
  route_table_id = aws_route_table.tgw[each.key].id
}

resource "aws_route_table_association" "firewall" {
  for_each       = var.inspection_azs
  subnet_id      = aws_subnet.inspection["${each.key}/firewall"].id
  route_table_id = aws_route_table.firewall[each.key].id
}

resource "aws_networkfirewall_firewall" "central" {
  count                             = local.firewall_enabled ? 1 : 0
  name                              = "${local.prefix}-central"
  vpc_id                            = aws_vpc.inspection.id
  firewall_policy_arn               = aws_networkfirewall_firewall_policy.central.arn
  delete_protection                 = var.inspection_mode == "bypass" ? false : var.firewall_protection.delete
  subnet_change_protection          = var.firewall_protection.subnet
  firewall_policy_change_protection = var.firewall_protection.policy

  dynamic "subnet_mapping" {
    for_each = var.inspection_azs
    content {
      subnet_id       = aws_subnet.inspection["${subnet_mapping.key}/firewall"].id
      ip_address_type = "IPV4"
    }
  }
  tags = { Name = "${local.prefix}-central" }
}

locals {
  # Each AZ key is known at plan time. Match subnet IDs as well as the API AZ name;
  # never infer physical AZ identity from a computed set's position.
  primary_endpoint_ids = local.firewall_enabled ? { for az_id in keys(var.inspection_azs) : az_id => one(flatten([
    for sync in one(aws_networkfirewall_firewall.central[0].firewall_status).sync_states : [
      for attachment in sync.attachment : attachment.endpoint_id
      if attachment.subnet_id == aws_subnet.inspection["${az_id}/firewall"].id
    ] if sync.availability_zone == data.aws_availability_zone.selected[az_id].name
  ])) } : {}
}

resource "aws_route" "to_firewall" {
  for_each               = local.firewall_enabled ? local.route_matrix : {}
  route_table_id         = aws_route_table.tgw[each.value.az_id].id
  destination_cidr_block = each.value.cidr
  vpc_endpoint_id        = local.primary_endpoint_ids[each.value.az_id]
  depends_on             = [aws_ec2_transit_gateway_vpc_attachment.inspection]
  lifecycle {
    precondition {
      condition     = local.primary_endpoint_ids[each.value.az_id] != null
      error_message = "Firewall has no unique primary endpoint for the selected physical AZ and subnet."
    }
  }
}

resource "aws_route" "to_tgw" {
  for_each               = local.route_matrix
  route_table_id         = aws_route_table.firewall[each.value.az_id].id
  destination_cidr_block = each.value.cidr
  transit_gateway_id     = aws_ec2_transit_gateway.central.id
  depends_on             = [aws_ec2_transit_gateway_vpc_attachment.inspection]
}
