resource "aws_vpc" "client" {
  cidr_block                       = var.vpc_cidr
  enable_dns_support               = true
  enable_dns_hostnames             = true
  assign_generated_ipv6_cidr_block = false
  tags                             = { Name = var.vpc_name }
  depends_on                       = [terraform_data.contract]
}

resource "aws_subnet" "private" {
  for_each                                       = local.subnets
  vpc_id                                         = aws_vpc.client.id
  availability_zone_id                           = each.value.az_id
  cidr_block                                     = each.value.cidr
  map_public_ip_on_launch                        = false
  assign_ipv6_address_on_creation                = false
  enable_resource_name_dns_aaaa_record_on_launch = false
  tags                                           = { Name = "${local.prefix}-${each.value.az_id}-${each.value.role}" }
}

resource "aws_route_table" "private" {
  for_each = local.subnets
  vpc_id   = aws_vpc.client.id
  tags     = { Name = "${local.prefix}-${each.value.az_id}-${each.value.role}" }
}

resource "aws_route_table_association" "private" {
  for_each       = local.subnets
  subnet_id      = aws_subnet.private[each.key].id
  route_table_id = aws_route_table.private[each.key].id
}

resource "aws_ec2_transit_gateway_vpc_attachment" "client" {
  count                                           = var.transit_gateway_id == null ? 0 : 1
  transit_gateway_id                              = data.aws_ec2_transit_gateway.central[0].id
  vpc_id                                          = aws_vpc.client.id
  subnet_ids                                      = [for az_id in sort(keys(var.azs)) : aws_subnet.private["${az_id}/tgw"].id]
  appliance_mode_support                          = "disable"
  ipv6_support                                    = "disable"
  dns_support                                     = "enable"
  transit_gateway_default_route_table_association = false
  transit_gateway_default_route_table_propagation = false
  tags                                            = { Name = local.prefix }
  lifecycle {
    # RAM consumers cannot configure/read these flags accurately. The central
    # TGW/accepter owns them; the data source checks its actual defaults.
    ignore_changes = [
      transit_gateway_default_route_table_association,
      transit_gateway_default_route_table_propagation,
    ]
  }
}

resource "aws_route" "remote" {
  for_each               = local.routes
  route_table_id         = aws_route_table.private["${each.value.az_id}/client"].id
  destination_cidr_block = each.value.cidr
  transit_gateway_id     = var.transit_gateway_id
  depends_on             = [aws_ec2_transit_gateway_vpc_attachment.client]
}
