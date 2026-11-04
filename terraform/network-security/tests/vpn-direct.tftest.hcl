override_data {
  override_during = plan
  target          = data.aws_availability_zone.selected["apne2-az1"]
  values          = { name = "ap-northeast-2d", zone_id = "apne2-az1", region = "ap-northeast-2", zone_type = "availability-zone" }
}
override_data {
  override_during = plan
  target          = data.aws_availability_zone.selected["apne2-az2"]
  values          = { name = "ap-northeast-2a", zone_id = "apne2-az2", region = "ap-northeast-2", zone_type = "availability-zone" }
}
override_resource {
  override_during = plan
  target          = aws_subnet.inspection["apne2-az1/firewall"]
  values          = { id = "subnet-00000000000000001" }
}
override_resource {
  override_during = plan
  target          = aws_subnet.inspection["apne2-az2/firewall"]
  values          = { id = "subnet-00000000000000002" }
}

mock_provider "aws" {
  source          = "./tests/mocks"
  override_during = plan
}

override_resource {
  target          = aws_ec2_transit_gateway_route_table.spoke
  values          = { id = "tgw-rtb-00000000000000031" }
  override_during = plan
}
override_resource {
  target          = aws_ec2_transit_gateway_route_table.inspected
  values          = { id = "tgw-rtb-00000000000000032" }
  override_during = plan
}

override_resource {
  target          = aws_vpn_connection.peer["if0"]
  values          = { transit_gateway_attachment_id = "tgw-attach-00000000000000021" }
  override_during = plan
}
override_resource {
  target          = aws_vpn_connection.peer["if1"]
  values          = { transit_gateway_attachment_id = "tgw-attach-00000000000000022" }
  override_during = plan
}

variables {
  shared_account_ids = ["444455556666"]
  spoke_attachments = {
    client = {
      account_id    = "444455556666", role = "Client", vpc_cidrs = ["10.10.0.0/16"]
      attachment_id = "tgw-attach-00000000000000011", acceptance_mode = "existing", reported_state = "available"
      az_ids        = ["apne2-az1", "apne2-az2"]
    }
  }
  vpn_networks = { gcp = { cidrs = ["10.30.1.0/24"], kind = "gcp" } }
  vpn_peers = {
    if0 = {
      network_key         = "gcp", public_ip = "1.1.1.1", bgp_asn = 65001, gcp_interface = 0
      tunnel1_inside_cidr = "169.254.10.0/30", tunnel2_inside_cidr = "169.254.10.4/30"
    }
    if1 = {
      network_key         = "gcp", public_ip = "8.8.8.8", bgp_asn = 65001, gcp_interface = 1
      tunnel1_inside_cidr = "169.254.10.8/30", tunnel2_inside_cidr = "169.254.10.12/30"
    }
  }
}

run "disabled_gcp_vpn_uses_bgp_without_firewall" {
  command = plan
  variables { inspection_mode = "disabled" }
  assert {
    condition     = length(aws_networkfirewall_firewall.central) == 0 && length(aws_route.to_firewall) == 0 && length(aws_networkfirewall_logging_configuration.central) == 0
    error_message = "Adding a GCP VPN in disabled mode must never recreate ANF or endpoints."
  }
  assert {
    condition     = keys(aws_ec2_transit_gateway_route.before_inspection) == ["10.10.0.0/16"] && output.inspection_route_targets["10.10.0.0/16"] == "tgw-attach-00000000000000011"
    error_message = "VPC routes stay direct; no static VPN destination may override propagated HA routes."
  }
  assert {
    condition     = length(aws_ec2_transit_gateway_route_table_propagation.vpn_direct) == 2 && alltrue([for propagation in aws_ec2_transit_gateway_route_table_propagation.vpn_direct : propagation.transit_gateway_route_table_id == aws_ec2_transit_gateway_route_table.spoke.id]) && aws_ec2_transit_gateway.central.vpn_ecmp_support == "enable"
    error_message = "Both VPN attachments must propagate into Spoke, preserving BGP withdrawal and ECMP."
  }
  assert {
    condition     = alltrue([for association in aws_ec2_transit_gateway_route_table_association.vpn : association.transit_gateway_route_table_id == aws_ec2_transit_gateway_route_table.spoke.id]) && length(aws_vpn_connection.peer) == 2
    error_message = "GCP-initiated traffic must enter the same direct Spoke route table."
  }
}

run "bypass_gcp_vpn_keeps_bgp_direct" {
  command = plan
  variables { inspection_mode = "bypass" }
  assert {
    condition     = length(aws_networkfirewall_firewall.central) == 1 && length(aws_ec2_transit_gateway_route_table_propagation.vpn_direct) == 2 && length(aws_ec2_transit_gateway_route.before_inspection) == 1
    error_message = "Bypass retains ANF but VPN traffic must use dynamic direct routes."
  }
}

run "enabled_gcp_vpn_keeps_original_inspection" {
  command = plan
  variables { inspection_mode = "enabled" }
  assert {
    condition     = length(aws_ec2_transit_gateway_route_table_propagation.vpn_direct) == 0 && length(aws_ec2_transit_gateway_route.before_inspection) == 2 && alltrue([for route in aws_ec2_transit_gateway_route.before_inspection : route.transit_gateway_attachment_id == aws_ec2_transit_gateway_vpc_attachment.inspection.id])
    error_message = "The existing enabled mode must retain inspection and prevent direct VPN propagation."
  }
}
