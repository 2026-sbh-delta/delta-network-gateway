resource "aws_ec2_transit_gateway" "central" {
  description                     = "Delta inspected private connectivity"
  amazon_side_asn                 = var.tgw_asn
  auto_accept_shared_attachments  = "disable"
  default_route_table_association = "disable"
  default_route_table_propagation = "disable"
  vpn_ecmp_support                = "enable"
  tags                            = { Name = "${local.prefix}-tgw" }
  depends_on                      = [terraform_data.contract]
}

resource "aws_ec2_transit_gateway_vpc_attachment" "inspection" {
  transit_gateway_id                              = aws_ec2_transit_gateway.central.id
  vpc_id                                          = aws_vpc.inspection.id
  subnet_ids                                      = [for az_id in sort(keys(var.inspection_azs)) : aws_subnet.inspection["${az_id}/tgw"].id]
  appliance_mode_support                          = "enable"
  ipv6_support                                    = "disable"
  transit_gateway_default_route_table_association = false
  transit_gateway_default_route_table_propagation = false
  tags                                            = { Name = "${local.prefix}-inspection" }
}

resource "aws_ec2_transit_gateway_route_table" "spoke" {
  transit_gateway_id = aws_ec2_transit_gateway.central.id
  tags               = { Name = "${local.prefix}-spoke" }
}

resource "aws_ec2_transit_gateway_route_table" "inspected" {
  transit_gateway_id = aws_ec2_transit_gateway.central.id
  tags               = { Name = "${local.prefix}-inspected" }
}

resource "aws_ec2_transit_gateway_route_table_association" "inspection" {
  transit_gateway_attachment_id  = aws_ec2_transit_gateway_vpc_attachment.inspection.id
  transit_gateway_route_table_id = aws_ec2_transit_gateway_route_table.inspected.id
}

data "aws_ec2_transit_gateway_vpc_attachment" "spoke" {
  for_each = var.spoke_attachments
  id       = each.value.attachment_id
  filter {
    name   = "state"
    values = each.value.acceptance_mode == "existing" ? ["available"] : ["pendingAcceptance", "available"]
  }
  lifecycle {
    postcondition {
      condition     = self.vpc_owner_id == each.value.account_id
      error_message = "Returned attachment does not belong to the declared consumer account."
    }
  }
}

resource "terraform_data" "spoke_contract" {
  for_each = var.spoke_attachments
  input    = data.aws_ec2_transit_gateway_vpc_attachment.spoke[each.key].id
  lifecycle {
    precondition {
      condition     = data.aws_ec2_transit_gateway_vpc_attachment.spoke[each.key].transit_gateway_id == aws_ec2_transit_gateway.central.id
      error_message = "The consumer attachment must target this central TGW."
    }
  }
}

resource "aws_ec2_transit_gateway_vpc_attachment_accepter" "spoke" {
  # Mode is an ownership choice, not a transient API state. Keep accept mode
  # after it becomes available; existing mode never creates an accepter.
  for_each                                        = { for key, spoke in var.spoke_attachments : key => spoke if spoke.acceptance_mode == "accept" }
  transit_gateway_attachment_id                   = data.aws_ec2_transit_gateway_vpc_attachment.spoke[each.key].id
  transit_gateway_default_route_table_association = false
  transit_gateway_default_route_table_propagation = false
  tags                                            = { Name = "${local.prefix}-${each.key}" }
  depends_on                                      = [terraform_data.spoke_contract]
  lifecycle {
    # Provider 6.71.0 destroys the actual consumer VPC attachment when an
    # accepter is destroyed. Protect against a mode/key change doing this.
    prevent_destroy = true
  }
}

resource "aws_ec2_transit_gateway_route_table_association" "spoke" {
  for_each                       = var.spoke_attachments
  transit_gateway_attachment_id  = data.aws_ec2_transit_gateway_vpc_attachment.spoke[each.key].id
  transit_gateway_route_table_id = aws_ec2_transit_gateway_route_table.spoke.id
  depends_on                     = [aws_ec2_transit_gateway_vpc_attachment_accepter.spoke, terraform_data.spoke_contract]
}

resource "aws_ec2_transit_gateway_route" "before_inspection" {
  for_each                       = local.inspected_cidrs
  destination_cidr_block         = each.key
  transit_gateway_route_table_id = aws_ec2_transit_gateway_route_table.spoke.id
  # Keep route tables, destinations, associations and resource addresses stable.
  # VPN bypass is rejected by terraform_data.contract before any writes.
  transit_gateway_attachment_id = var.inspection_mode == "enabled" ? aws_ec2_transit_gateway_vpc_attachment.inspection.id : lookup(
    local.direct_spoke_targets, each.key, aws_ec2_transit_gateway_vpc_attachment.inspection.id
  )
}

resource "aws_ec2_transit_gateway_route" "to_spoke" {
  for_each                       = local.spoke_routes
  destination_cidr_block         = each.value.cidr
  transit_gateway_route_table_id = aws_ec2_transit_gateway_route_table.inspected.id
  transit_gateway_attachment_id  = data.aws_ec2_transit_gateway_vpc_attachment.spoke[each.value.spoke_key].id
  depends_on                     = [aws_ec2_transit_gateway_route_table_association.spoke]
}

resource "aws_ec2_transit_gateway_route_table_association" "vpn" {
  for_each                       = var.vpn_peers
  transit_gateway_attachment_id  = aws_vpn_connection.peer[each.key].transit_gateway_attachment_id
  transit_gateway_route_table_id = aws_ec2_transit_gateway_route_table.spoke.id
}

resource "aws_ec2_transit_gateway_route_table_propagation" "vpn" {
  for_each                       = var.vpn_peers
  transit_gateway_attachment_id  = aws_vpn_connection.peer[each.key].transit_gateway_attachment_id
  transit_gateway_route_table_id = aws_ec2_transit_gateway_route_table.inspected.id
}
