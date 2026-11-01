resource "aws_customer_gateway" "peer" {
  for_each         = var.vpn_peers
  bgp_asn          = each.value.bgp_asn < 2147483648 ? each.value.bgp_asn : null
  bgp_asn_extended = each.value.bgp_asn >= 2147483648 ? each.value.bgp_asn : null
  ip_address       = each.value.public_ip
  type             = "ipsec.1"
  tags             = { Name = "${local.prefix}-${each.key}" }
  depends_on       = [terraform_data.contract]
}

resource "aws_vpn_connection" "peer" {
  for_each            = var.vpn_peers
  customer_gateway_id = aws_customer_gateway.peer[each.key].id
  transit_gateway_id  = aws_ec2_transit_gateway.central.id
  type                = "ipsec.1"
  static_routes_only  = false
  enable_acceleration = false

  # AWS generates PSKs. They are sensitive state data and are NEVER exported.
  preshared_key_storage = "Standard"
  tunnel1_inside_cidr   = each.value.tunnel1_inside_cidr
  tunnel2_inside_cidr   = each.value.tunnel2_inside_cidr
  tunnel1_ike_versions  = ["ikev2"]
  tunnel2_ike_versions  = ["ikev2"]

  # Single interoperable transform avoids oversized GCP IKE proposals/rekeying.
  tunnel1_phase1_encryption_algorithms = ["AES256"]
  tunnel2_phase1_encryption_algorithms = ["AES256"]
  tunnel1_phase1_integrity_algorithms  = ["SHA2-256"]
  tunnel2_phase1_integrity_algorithms  = ["SHA2-256"]
  tunnel1_phase1_dh_group_numbers      = [14]
  tunnel2_phase1_dh_group_numbers      = [14]
  tunnel1_phase2_encryption_algorithms = ["AES256"]
  tunnel2_phase2_encryption_algorithms = ["AES256"]
  tunnel1_phase2_integrity_algorithms  = ["SHA2-256"]
  tunnel2_phase2_integrity_algorithms  = ["SHA2-256"]
  tunnel1_phase2_dh_group_numbers      = [14]
  tunnel2_phase2_dh_group_numbers      = [14]
  tags                                 = { Name = "${local.prefix}-${each.key}" }
}
