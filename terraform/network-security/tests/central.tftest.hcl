# Run only with terraform test -var-file=terraform.tfvars.example.
# Every run is command=plan and every AWS provider is mocked.
mock_provider "aws" {
  source          = "./tests/mocks"
  override_during = plan
}

run "bypass_keeps_firewall_and_directs_both_vpcs" {
  command = plan
  variables {
    inspection_mode    = "bypass"
    shared_account_ids = ["444455556666"]
    spoke_attachments = {
      client = {
        account_id    = "444455556666", role = "Client", vpc_cidrs = ["10.10.0.0/16"]
        attachment_id = "tgw-attach-00000000000000011", acceptance_mode = "accept", reported_state = "available"
        az_ids        = ["apne2-az1", "apne2-az2"]
      }
      server = {
        account_id    = "444455556666", role = "Backend", vpc_cidrs = ["10.20.0.0/16"]
        attachment_id = "tgw-attach-00000000000000012", acceptance_mode = "accept", reported_state = "available"
        az_ids        = ["apne2-az1", "apne2-az2"]
      }
    }
  }
  assert {
    condition     = output.inspection_mode == "bypass" && length(output.inspection_route_targets) == 2 && output.inspection_route_targets["10.10.0.0/16"] == "tgw-attach-00000000000000011" && output.inspection_route_targets["10.20.0.0/16"] == "tgw-attach-00000000000000012"
    error_message = "Bypass must route each destination directly to its owning VPC in both directions."
  }
  assert {
    condition     = length(aws_networkfirewall_firewall.central) == 1 && !aws_networkfirewall_firewall.central[0].delete_protection && length(aws_route.to_firewall) == 4 && length(aws_networkfirewall_logging_configuration.central) == 1 && contains(keys(aws_ram_resource_association.central), "firewall")
    error_message = "The staging mode must retain the firewall, endpoints, routes, logging and sharing while disabling delete protection."
  }
  assert {
    condition     = length(aws_ec2_transit_gateway_vpc_attachment_accepter.spoke) == 2 && alltrue([for association in aws_ec2_transit_gateway_route_table_association.spoke : association.transit_gateway_route_table_id == aws_ec2_transit_gateway_route_table.spoke.id])
    error_message = "Bypass must retain the existing attachment ownership and route-table associations."
  }
}

run "disabled_removes_endpoints_preserves_foundation" {
  command = plan
  variables {
    inspection_mode    = "disabled"
    shared_account_ids = ["444455556666"]
    spoke_attachments = {
      client = {
        account_id    = "444455556666", role = "Client", vpc_cidrs = ["10.10.0.0/16"]
        attachment_id = "tgw-attach-00000000000000011", acceptance_mode = "accept", reported_state = "available"
        az_ids        = ["apne2-az1", "apne2-az2"]
      }
      server = {
        account_id    = "444455556666", role = "Backend", vpc_cidrs = ["10.20.0.0/16"]
        attachment_id = "tgw-attach-00000000000000012", acceptance_mode = "accept", reported_state = "available"
        az_ids        = ["apne2-az1", "apne2-az2"]
      }
    }
  }
  assert {
    condition     = length(aws_networkfirewall_firewall.central) == 0 && length(aws_route.to_firewall) == 0 && length(aws_networkfirewall_logging_configuration.central) == 0 && !contains(keys(aws_ram_resource_association.central), "firewall") && output.firewall_arn == null && length(output.primary_endpoint_ids_by_az_id) == 0
    error_message = "Disabled mode must remove all billed firewall endpoints and their logging, route and sharing references."
  }
  assert {
    condition     = output.inspection_route_targets["10.10.0.0/16"] == "tgw-attach-00000000000000011" && output.inspection_route_targets["10.20.0.0/16"] == "tgw-attach-00000000000000012" && length(aws_ec2_transit_gateway_route.to_spoke) == 2 && length(aws_route.to_tgw) == 4
    error_message = "Both VPC routes must stay direct while the inspection return routes remain available for restoration."
  }
  assert {
    condition     = length(aws_subnet.inspection) == 4 && length(aws_route_table.tgw) == 2 && length(aws_route_table.firewall) == 2 && length(aws_cloudwatch_log_group.firewall) == 2 && length(aws_ram_resource_share.central) == 2 && length(aws_ram_principal_association.consumer) == 2 && contains(keys(aws_ram_resource_association.central), "tgw") && aws_networkfirewall_firewall_policy.central.name == "delta-lab-policy" && aws_networkfirewall_rule_group.central.name == "delta-lab-stateful"
    error_message = "Keep the existing network, policies, rules, logs, shares and consumer permissions to support restoration."
  }
}

run "disabled_without_spokes" {
  command = plan
  variables {
    inspection_mode = "disabled"
  }
  assert {
    condition     = length(aws_networkfirewall_firewall.central) == 0 && length(output.inspection_route_targets) == 0 && length(output.primary_endpoint_ids_by_az_id) == 0
    error_message = "An unused central foundation must also support endpoint-free disabled mode."
  }
}

run "reject_unknown_inspection_mode" {
  command = plan
  variables {
    inspection_mode = "stop"
  }
  expect_failures = [var.inspection_mode]
}

run "reject_bypass_with_vpn_network" {
  command = plan
  variables {
    inspection_mode = "bypass"
    vpn_networks    = { onprem = { cidrs = ["10.90.0.0/16"], kind = "onprem" } }
  }
  expect_failures = [terraform_data.contract]
}

run "reject_disabled_with_vpn_network" {
  command = plan
  variables {
    inspection_mode = "disabled"
    vpn_networks    = { onprem = { cidrs = ["10.90.0.0/16"], kind = "onprem" } }
  }
  expect_failures = [terraform_data.contract]
}

run "same_account_client_server" {
  command = plan
  variables {
    shared_account_ids = ["444455556666"]
    spoke_attachments = {
      aws_client = {
        account_id    = "444455556666", role = "Client", vpc_cidrs = ["10.10.0.0/16"]
        attachment_id = "tgw-attach-00000000000000011", acceptance_mode = "accept", reported_state = "available"
        az_ids        = ["apne2-az1", "apne2-az2"]
      }
      aws_server = {
        account_id    = "444455556666", role = "Backend", vpc_cidrs = ["10.20.0.0/16"]
        attachment_id = "tgw-attach-00000000000000012", acceptance_mode = "accept", reported_state = "pendingAcceptance"
        az_ids        = ["apne2-az1", "apne2-az2"]
      }
    }
    allowed_flows = {
      aws_client_to_server = {
        source_cidrs = ["10.10.1.143/32"], destination_cidrs = ["10.20.1.10/32"]
        protocol     = "tcp", destination_ports = [8080], sid = 1000001
      }
    }
    suricata_rules = {
      http_test_marker = {
        sid  = 2000001
        rule = "alert http $HOME_NET any -> $EXTERNAL_NET any (msg:\"Delta HTTP test marker\"; flow:established,to_server; http.uri; content:\"/delta-test-marker\"; sid:2000001; rev:1;)"
      }
    }
  }
  assert {
    condition     = length(aws_ec2_transit_gateway_route.before_inspection) == 2 && alltrue([for route in aws_ec2_transit_gateway_route.before_inspection : route.transit_gateway_attachment_id == aws_ec2_transit_gateway_vpc_attachment.inspection.id]) && length(aws_ec2_transit_gateway_route.to_spoke) == 2 && length(aws_route.to_firewall) == 4 && length(aws_route.to_tgw) == 4 && length(aws_vpn_connection.peer) == 0
    error_message = "Both same-account VPCs must traverse inspection with explicit return routes and no VPN."
  }
  assert {
    condition     = alltrue([for association in aws_ec2_transit_gateway_route_table_association.spoke : association.transit_gateway_route_table_id == aws_ec2_transit_gateway_route_table.spoke.id]) && length(aws_ec2_transit_gateway_route_table_association.spoke) == 2 && length(aws_ec2_transit_gateway_route_table_propagation.vpn) == 0
    error_message = "Both source VPCs must associate with Spoke without direct propagation."
  }
  assert {
    condition     = contains(local.home_net, "10.10.0.0/16") && contains(local.home_net, "10.20.0.0/16") && strcontains(local.rules_string, "[10.20.1.10/32] [8080]") && !strcontains(local.rules_string, "8081") && strcontains(local.rules_string, "sid:2000001;") && endswith(local.rules_string, "sid:9000000; rev:1;)")
    error_message = "Allow only the 8080 control, keep the observation alert and final deny, and inspect both VPC CIDRs."
  }
}
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
override_resource {
  override_during = plan
  target          = aws_ec2_transit_gateway_route_table.spoke
  values          = { id = "tgw-rtb-00000000000000001" }
}
override_resource {
  override_during = plan
  target          = aws_ec2_transit_gateway_route_table.inspected
  values          = { id = "tgw-rtb-00000000000000002" }
}

run "central_only" {
  command = plan
  assert {
    condition     = length(aws_subnet.inspection) == 4 && length(aws_route_table.tgw) == 2 && length(aws_route_table.firewall) == 2 && length(aws_route_table_association.tgw) == 2 && length(aws_route_table_association.firewall) == 2
    error_message = "Two AZs need four dedicated subnets and four explicit route-table associations."
  }
  assert {
    condition     = alltrue([for subnet in values(aws_subnet.inspection) : !subnet.map_public_ip_on_launch]) && length(aws_networkfirewall_firewall.central[0].subnet_mapping) == 2
    error_message = "Dedicated subnets must not auto-assign public IPs and each AZ must have a primary endpoint."
  }
  assert {
    condition     = aws_ec2_transit_gateway.central.auto_accept_shared_attachments == "disable" && aws_ec2_transit_gateway.central.default_route_table_association == "disable" && aws_ec2_transit_gateway.central.default_route_table_propagation == "disable" && aws_ec2_transit_gateway_vpc_attachment.inspection.appliance_mode_support == "enable"
    error_message = "TGW defaults must not permit inspection bypass."
  }
  assert {
    condition     = length(aws_ec2_transit_gateway_route.before_inspection) == 0 && length(aws_vpn_connection.peer) == 0 && length(aws_ec2_transit_gateway_vpc_attachment_accepter.spoke) == 0
    error_message = "Empty inputs must support a central-only foundation without example connections."
  }
  assert {
    condition     = length(aws_ram_resource_share.central) == 2 && aws_ram_resource_association.central["firewall"].resource_arn == aws_networkfirewall_firewall.central[0].arn && !aws_ram_resource_share.central["firewall"].allow_external_principals
    error_message = "Share the firewall itself separately from the TGW; external sharing defaults off."
  }
  assert {
    condition     = output.primary_endpoint_ids_by_az_id["apne2-az1"] == "vpce-00000000000000001" && output.primary_endpoint_ids_by_az_id["apne2-az2"] == "vpce-00000000000000002"
    error_message = "Endpoint identity must follow physical AZ IDs despite nonmatching AZ names and set order."
  }
  assert {
    condition     = aws_networkfirewall_firewall.central[0].delete_protection && aws_networkfirewall_firewall.central[0].subnet_change_protection && aws_networkfirewall_firewall.central[0].firewall_policy_change_protection && alltrue([for group in values(aws_cloudwatch_log_group.firewall) : group.retention_in_days == 30 && group.skip_destroy])
    error_message = "Protection and default log preservation must be enabled."
  }
  assert {
    condition     = one(aws_networkfirewall_firewall_policy.central.firewall_policy).stateless_default_actions == toset(["aws:forward_to_sfe"]) && one(aws_networkfirewall_firewall_policy.central.firewall_policy).stateless_fragment_default_actions == toset(["aws:forward_to_sfe"]) && !can(regex("(?m)^pass ", local.rules_string)) && endswith(local.rules_string, "sid:9000000; rev:1;)")
    error_message = "All packets must reach the stateful engine with a mandatory final deny and no pass shortcut."
  }
}

run "registered_spokes" {
  command = plan
  variables {
    shared_account_ids = [
      "444455556666"
    ]
    spoke_attachments = {
      "alb" : {
        "account_id" : "444455556666",
        "role" : "ALB",
        "vpc_cidrs" : [
          "10.10.0.0/16"
        ],
        "attachment_id" : "tgw-attach-00000000000000011",
        "acceptance_mode" : "accept",
        "reported_state" : "pendingAcceptance",
        "az_ids" : [
          "apne2-az1",
          "apne2-az2"
        ]
      },
      "backend" : {
        "account_id" : "444455556666",
        "role" : "Backend",
        "vpc_cidrs" : [
          "10.20.0.0/16"
        ],
        "attachment_id" : "tgw-attach-00000000000000012",
        "acceptance_mode" : "existing",
        "reported_state" : "available",
        "az_ids" : [
          "apne2-az1",
          "apne2-az2"
        ]
      }
    }
  }

  assert {
    condition     = length(aws_ec2_transit_gateway_vpc_attachment_accepter.spoke) == 1 && contains(keys(aws_ec2_transit_gateway_vpc_attachment_accepter.spoke), "alb")
    error_message = "Only accept-mode attachments may have an accepter."
  }
  assert {
    condition     = length(aws_ec2_transit_gateway_route.before_inspection) == 2 && length(aws_route.to_firewall) == 4 && length(aws_route.to_tgw) == 4 && length(aws_ec2_transit_gateway_route.to_spoke) == 2
    error_message = "Every declared VPC CIDR needs all inspection legs exactly once."
  }
  assert {
    condition     = alltrue([for route in values(aws_ec2_transit_gateway_route.before_inspection) : route.transit_gateway_attachment_id == aws_ec2_transit_gateway_vpc_attachment.inspection.id && route.transit_gateway_route_table_id == aws_ec2_transit_gateway_route_table.spoke.id]) && alltrue([for route in values(aws_ec2_transit_gateway_route.to_spoke) : route.transit_gateway_route_table_id == aws_ec2_transit_gateway_route_table.inspected.id])
    error_message = "Spoke routes must target only inspection; direct destination routes belong only to Inspected."
  }
  assert {
    condition     = alltrue([for key, route in aws_route.to_firewall : route.vpc_endpoint_id == output.primary_endpoint_ids_by_az_id[local.route_matrix[key].az_id]])
    error_message = "Each TGW subnet must send both request/return destinations to its own AZ endpoint."
  }
  assert {
    condition     = contains(local.home_net, "10.10.0.0/16") && contains(local.home_net, "10.20.0.0/16")
    error_message = "HOME_NET must include actual inspected VPCs."
  }
}

run "stable_accept_ownership" {
  command = plan
  variables {
    shared_account_ids = [
      "444455556666"
    ]
    spoke_attachments = {
      "alb" : {
        "account_id" : "444455556666",
        "role" : "ALB",
        "vpc_cidrs" : [
          "10.10.0.0/16"
        ],
        "attachment_id" : "tgw-attach-00000000000000011",
        "acceptance_mode" : "accept",
        "reported_state" : "available",
        "az_ids" : [
          "apne2-az1",
          "apne2-az2"
        ]
      }
    }
  }

  assert {
    condition     = length(aws_ec2_transit_gateway_vpc_attachment_accepter.spoke) == 1
    error_message = "An accepted attachment must retain its accepter resource address."
  }
}

run "gcp_ha" {
  command = plan
  variables {
    vpn_networks = {
      "gcp" : {
        "cidrs" : [
          "10.30.0.0/16"
        ],
        "kind" : "gcp"
      }
    }
    vpn_peers = {
      "if0" : {
        "network_key" : "gcp",
        "public_ip" : "1.1.1.1",
        "bgp_asn" : 65001,
        "tunnel1_inside_cidr" : "169.254.10.0/30",
        "tunnel2_inside_cidr" : "169.254.10.4/30",
        "gcp_interface" : 0
      },
      "if1" : {
        "network_key" : "gcp",
        "public_ip" : "8.8.8.8",
        "bgp_asn" : 65001,
        "tunnel1_inside_cidr" : "169.254.10.8/30",
        "tunnel2_inside_cidr" : "169.254.10.12/30",
        "gcp_interface" : 1
      }
    }
  }

  assert {
    condition     = length(aws_vpn_connection.peer) == 2 && length(aws_customer_gateway.peer) == 2 && length(aws_ec2_transit_gateway_route.before_inspection) == 1
    error_message = "GCP HA uses two connections/four tunnels, but one logical destination route."
  }
  assert {
    condition     = length(aws_ec2_transit_gateway_route_table_propagation.vpn) == 2 && alltrue([for p in values(aws_ec2_transit_gateway_route_table_propagation.vpn) : p.transit_gateway_route_table_id == aws_ec2_transit_gateway_route_table.inspected.id]) && alltrue([for a in values(aws_ec2_transit_gateway_route_table_association.vpn) : a.transit_gateway_route_table_id == aws_ec2_transit_gateway_route_table.spoke.id])
    error_message = "VPN ingress uses Spoke, and BGP propagation is confined to Inspected."
  }
  assert {
    condition = alltrue([for handoff in values(output.vpn_handoff) :
      length(setintersection(toset(keys(handoff)), toset(["preshared_key", "tunnel1_preshared_key", "tunnel2_preshared_key", "customer_gateway_configuration"]))) == 0 &&
      alltrue([for tunnel in values(handoff.tunnels) : length(setintersection(toset(keys(tunnel)), toset(["preshared_key", "psk"]))) == 0])
    ])
    error_message = "Remote handoff must never export PSKs or native VPN configuration."
  }
}

run "extended_asn" {
  command = plan
  variables {
    vpn_networks = {
      "branch" : {
        "cidrs" : [
          "10.30.0.0/16"
        ],
        "kind" : "onprem"
      }
    }
    vpn_peers = {
      "branch" : {
        "network_key" : "branch",
        "public_ip" : "1.1.1.1",
        "bgp_asn" : 4200000001,
        "tunnel1_inside_cidr" : "169.254.10.0/30",
        "tunnel2_inside_cidr" : "169.254.10.4/30"
      }
    }
  }

  assert {
    condition     = tonumber(aws_customer_gateway.peer["branch"].bgp_asn_extended) == 4200000001
    error_message = "32-bit ASNs must use the provider extended-ASN field."
  }
}

run "tcp_udp_icmp_and_signature" {
  command = plan
  variables {
    allowed_flows = {
      "tcp" : {
        "source_cidrs" : [
          "10.30.0.0/16"
        ],
        "destination_cidrs" : [
          "10.20.0.0/16"
        ],
        "protocol" : "tcp",
        "destination_ports" : [
          443
        ],
        "sid" : 1000001
      },
      "udp" : {
        "source_cidrs" : [
          "10.30.0.0/16"
        ],
        "destination_cidrs" : [
          "10.20.0.0/16"
        ],
        "protocol" : "udp",
        "destination_ports" : [
          53
        ],
        "sid" : 1000002
      },
      "icmp" : {
        "source_cidrs" : [
          "10.30.0.0/16"
        ],
        "destination_cidrs" : [
          "10.20.0.0/16"
        ],
        "protocol" : "icmp",
        "destination_ports" : [],
        "sid" : 1000003,
        "icmp_type" : 8
      }
    }
    suricata_rules = {
      "marker" : {
        "sid" : 2000001,
        "rule" : "alert http $HOME_NET any -> $EXTERNAL_NET any (msg:\"test marker\"; flow:established,to_server; http.uri; content:\"/delta-test-marker\"; sid:2000001; rev:1;)"
      }
    }
  }

  assert {
    condition     = length(local.flow_rules) == 3 && startswith(local.rules_string, "alert http ") && !can(regex("(?m)^pass ", local.rules_string))
    error_message = "Custom signatures must precede all nonterminating protocol allow markers."
  }
  assert {
    condition     = alltrue([for rule in local.flow_rules : can(regex("flow:to_server;", rule)) && can(regex("flowbits:noalert;", rule))]) && can(regex("itype:8;", local.rules_string))
    error_message = "Explicit protocol rules must mark only initiated flows and represent ICMP without ports."
  }
}

run "reject_overlapping_subnets" {
  command = plan
  variables {
    inspection_azs = {
      "apne2-az1" : {
        "tgw_subnet_cidr" : "10.250.0.0/28",
        "firewall_subnet_cidr" : "10.250.0.16/28"
      },
      "apne2-az2" : {
        "tgw_subnet_cidr" : "10.250.0.0/28",
        "firewall_subnet_cidr" : "10.250.0.48/28"
      }
    }
  }
  expect_failures = [terraform_data.contract]
}

run "reject_outside_vpc_subnet" {
  command = plan
  variables {
    inspection_azs = {
      "apne2-az1" : {
        "tgw_subnet_cidr" : "10.250.0.0/28",
        "firewall_subnet_cidr" : "10.250.0.16/28"
      },
      "apne2-az2" : {
        "tgw_subnet_cidr" : "10.251.0.0/28",
        "firewall_subnet_cidr" : "10.250.0.48/28"
      }
    }
  }
  expect_failures = [terraform_data.contract]
}

run "reject_overlapping_remote_networks" {
  command = plan
  variables {
    shared_account_ids = [
      "444455556666"
    ]
    spoke_attachments = {
      "alb" : {
        "account_id" : "444455556666",
        "role" : "ALB",
        "vpc_cidrs" : [
          "10.10.0.0/16"
        ],
        "attachment_id" : "tgw-attach-00000000000000011",
        "acceptance_mode" : "accept",
        "reported_state" : "pendingAcceptance",
        "az_ids" : [
          "apne2-az1",
          "apne2-az2"
        ]
      },
      "backend" : {
        "account_id" : "444455556666",
        "role" : "Backend",
        "vpc_cidrs" : [
          "10.10.1.0/24"
        ],
        "attachment_id" : "tgw-attach-00000000000000012",
        "acceptance_mode" : "existing",
        "reported_state" : "available",
        "az_ids" : [
          "apne2-az1",
          "apne2-az2"
        ]
      }
    }
  }
  expect_failures = [terraform_data.contract]
}

run "reject_duplicate_attachments" {
  command = plan
  variables {
    shared_account_ids = [
      "444455556666"
    ]
    spoke_attachments = {
      "alb" : {
        "account_id" : "444455556666",
        "role" : "ALB",
        "vpc_cidrs" : [
          "10.10.0.0/16"
        ],
        "attachment_id" : "tgw-attach-00000000000000011",
        "acceptance_mode" : "accept",
        "reported_state" : "pendingAcceptance",
        "az_ids" : [
          "apne2-az1",
          "apne2-az2"
        ]
      },
      "backend" : {
        "account_id" : "444455556666",
        "role" : "Backend",
        "vpc_cidrs" : [
          "10.20.0.0/16"
        ],
        "attachment_id" : "tgw-attach-00000000000000011",
        "acceptance_mode" : "existing",
        "reported_state" : "available",
        "az_ids" : [
          "apne2-az1",
          "apne2-az2"
        ]
      }
    }
  }
  expect_failures = [var.spoke_attachments]
}

run "reject_unsupported_spoke_az" {
  command = plan
  variables {
    shared_account_ids = [
      "444455556666"
    ]
    spoke_attachments = {
      "alb" : {
        "account_id" : "444455556666",
        "role" : "ALB",
        "vpc_cidrs" : [
          "10.10.0.0/16"
        ],
        "attachment_id" : "tgw-attach-00000000000000011",
        "acceptance_mode" : "accept",
        "reported_state" : "pendingAcceptance",
        "az_ids" : [
          "apne2-az3"
        ]
      }
    }
  }
  expect_failures = [var.spoke_attachments]
}

run "reject_unshared_account" {
  command = plan
  variables {
    spoke_attachments = {
      "alb" : {
        "account_id" : "444455556666",
        "role" : "ALB",
        "vpc_cidrs" : [
          "10.10.0.0/16"
        ],
        "attachment_id" : "tgw-attach-00000000000000011",
        "acceptance_mode" : "accept",
        "reported_state" : "pendingAcceptance",
        "az_ids" : [
          "apne2-az1",
          "apne2-az2"
        ]
      }
    }
  }
  expect_failures = [var.spoke_attachments]
}

run "reject_existing_must_be_accepted" {
  command = plan
  variables {
    shared_account_ids = [
      "444455556666"
    ]
    spoke_attachments = {
      "backend" : {
        "account_id" : "444455556666",
        "role" : "Backend",
        "vpc_cidrs" : [
          "10.20.0.0/16"
        ],
        "attachment_id" : "tgw-attach-00000000000000012",
        "acceptance_mode" : "existing",
        "reported_state" : "pendingAcceptance",
        "az_ids" : [
          "apne2-az1",
          "apne2-az2"
        ]
      }
    }
  }
  expect_failures = [var.spoke_attachments]
}

run "reject_duplicate_sids" {
  command = plan
  variables {
    allowed_flows = {
      "one" : {
        "source_cidrs" : [
          "10.30.0.0/16"
        ],
        "destination_cidrs" : [
          "10.20.0.0/16"
        ],
        "protocol" : "tcp",
        "destination_ports" : [
          443
        ],
        "sid" : 1000001
      },
      "two" : {
        "source_cidrs" : [
          "10.30.0.0/16"
        ],
        "destination_cidrs" : [
          "10.20.0.0/16"
        ],
        "protocol" : "tcp",
        "destination_ports" : [
          443
        ],
        "sid" : 1000001
      }
    }
  }
  expect_failures = [var.allowed_flows]
}

run "reject_invalid_port" {
  command = plan
  variables {
    allowed_flows = {
      "one" : {
        "source_cidrs" : [
          "10.30.0.0/16"
        ],
        "destination_cidrs" : [
          "10.20.0.0/16"
        ],
        "protocol" : "tcp",
        "destination_ports" : [
          65536
        ],
        "sid" : 1000001
      }
    }
  }
  expect_failures = [var.allowed_flows]
}

run "reject_invalid_flow_cidr" {
  command = plan
  variables {
    allowed_flows = {
      "one" : {
        "source_cidrs" : [
          "bad-cidr"
        ],
        "destination_cidrs" : [
          "10.20.0.0/16"
        ],
        "protocol" : "tcp",
        "destination_ports" : [
          443
        ],
        "sid" : 1000001
      }
    }
  }
  expect_failures = [terraform_data.contract]
}

run "reject_reserved_inside_cidr" {
  command = plan
  variables {
    vpn_networks = {
      "branch" : {
        "cidrs" : [
          "10.30.0.0/16"
        ],
        "kind" : "onprem"
      }
    }
    vpn_peers = {
      "branch" : {
        "network_key" : "branch",
        "public_ip" : "1.1.1.1",
        "bgp_asn" : 65001,
        "tunnel1_inside_cidr" : "169.254.0.0/30",
        "tunnel2_inside_cidr" : "169.254.10.4/30"
      }
    }
  }
  expect_failures = [var.vpn_peers]
}

run "reject_duplicate_inside_cidr" {
  command = plan
  variables {
    vpn_networks = {
      "branch" : {
        "cidrs" : [
          "10.30.0.0/16"
        ],
        "kind" : "onprem"
      }
    }
    vpn_peers = {
      "branch" : {
        "network_key" : "branch",
        "public_ip" : "1.1.1.1",
        "bgp_asn" : 65001,
        "tunnel1_inside_cidr" : "169.254.10.0/30",
        "tunnel2_inside_cidr" : "169.254.10.0/30"
      }
    }
  }
  expect_failures = [var.vpn_peers]
}

run "reject_invalid_asn" {
  command = plan
  variables {
    vpn_networks = {
      "branch" : {
        "cidrs" : [
          "10.30.0.0/16"
        ],
        "kind" : "onprem"
      }
    }
    vpn_peers = {
      "branch" : {
        "network_key" : "branch",
        "public_ip" : "1.1.1.1",
        "bgp_asn" : 64512,
        "tunnel1_inside_cidr" : "169.254.10.0/30",
        "tunnel2_inside_cidr" : "169.254.10.4/30"
      }
    }
  }
  expect_failures = [var.vpn_peers]
}

run "reject_private_gateway_ip" {
  command = plan
  variables {
    vpn_networks = {
      "branch" : {
        "cidrs" : [
          "10.30.0.0/16"
        ],
        "kind" : "onprem"
      }
    }
    vpn_peers = {
      "branch" : {
        "network_key" : "branch",
        "public_ip" : "10.0.0.1",
        "bgp_asn" : 65001,
        "tunnel1_inside_cidr" : "169.254.10.0/30",
        "tunnel2_inside_cidr" : "169.254.10.4/30"
      }
    }
  }
  expect_failures = [terraform_data.contract]
}

run "reject_documentation_gateway_ip" {
  command = plan
  variables {
    vpn_networks = {
      "branch" : {
        "cidrs" : [
          "10.30.0.0/16"
        ],
        "kind" : "onprem"
      }
    }
    vpn_peers = {
      "branch" : {
        "network_key" : "branch",
        "public_ip" : "203.0.113.10",
        "bgp_asn" : 65001,
        "tunnel1_inside_cidr" : "169.254.10.0/30",
        "tunnel2_inside_cidr" : "169.254.10.4/30"
      }
    }
  }
  expect_failures = [terraform_data.contract]
}

run "reject_incomplete_gcp_ha" {
  command = plan
  variables {
    vpn_networks = {
      "gcp" : {
        "cidrs" : [
          "10.30.0.0/16"
        ],
        "kind" : "gcp"
      }
    }
    vpn_peers = {
      "if0" : {
        "network_key" : "gcp",
        "public_ip" : "1.1.1.1",
        "bgp_asn" : 65001,
        "tunnel1_inside_cidr" : "169.254.10.0/30",
        "tunnel2_inside_cidr" : "169.254.10.4/30",
        "gcp_interface" : 0
      }
    }
  }
  expect_failures = [terraform_data.contract]
}

run "reject_default_route_forbidden" {
  command = plan
  variables {
    vpn_networks = {
      "branch" : {
        "cidrs" : [
          "0.0.0.0/0"
        ],
        "kind" : "onprem"
      }
    }
    vpn_peers = {
      "branch" : {
        "network_key" : "branch",
        "public_ip" : "1.1.1.1",
        "bgp_asn" : 65001,
        "tunnel1_inside_cidr" : "169.254.10.0/30",
        "tunnel2_inside_cidr" : "169.254.10.4/30"
      }
    }
  }
  expect_failures = [terraform_data.contract]
}

run "reject_custom_pass_forbidden" {
  command = plan
  variables {
    suricata_rules = {
      "bad" : {
        "sid" : 2000001,
        "rule" : "pass tcp any any -> any any (sid:2000001;)"
      }
    }
  }
  expect_failures = [var.suricata_rules]
}

run "reject_custom_flowbit_mutation_forbidden" {
  command = plan
  variables {
    suricata_rules = {
      "bad" : {
        "sid" : 2000001,
        "rule" : "alert tcp any any -> any any (flowbits:set,delta_allowed; sid:2000001;)"
      }
    }
  }
  expect_failures = [var.suricata_rules]
}

run "reject_malformed_account_id" {
  command = plan
  variables {
    central_account_id = "123"
  }
  expect_failures = [var.central_account_id]
}

run "reject_wrong_authenticated_account" {
  command = plan
  override_data {
    target = data.aws_caller_identity.current
    values = { account_id = "999900001111" }
  }
  expect_failures = [terraform_data.contract]
}
run "reject_wrong_az_region" {
  command = plan
  override_data {
    target = data.aws_availability_zone.selected["apne2-az1"]
    values = { name = "us-east-1a", zone_id = "apne2-az1", region = "us-east-1", zone_type = "availability-zone" }
  }
  expect_failures = [terraform_data.contract]
}

run "reject_missing_primary_endpoint" {
  command = plan
  override_resource {
    target          = aws_networkfirewall_firewall.central[0]
    override_during = plan
    values = {
      arn = "arn:aws:network-firewall:ap-northeast-2:111122223333:firewall/mock"
      firewall_status = [{
        sync_states = [{
          availability_zone = "ap-northeast-2d"
          attachment        = [{ endpoint_id = "vpce-00000000000000001", subnet_id = "subnet-00000000000000001" }]
        }]
      }]
    }
  }
  expect_failures = [output.primary_endpoint_ids_by_az_id]
}
run "reject_duplicate_primary_endpoint_id" {
  command = plan
  override_resource {
    target          = aws_networkfirewall_firewall.central[0]
    override_during = plan
    values = {
      arn = "arn:aws:network-firewall:ap-northeast-2:111122223333:firewall/mock"
      firewall_status = [{
        sync_states = [
          { availability_zone = "ap-northeast-2d", attachment = [{ endpoint_id = "vpce-00000000000000001", subnet_id = "subnet-00000000000000001" }] },
          { availability_zone = "ap-northeast-2a", attachment = [{ endpoint_id = "vpce-00000000000000001", subnet_id = "subnet-00000000000000002" }] }
        ]
      }]
    }
  }
  expect_failures = [output.primary_endpoint_ids_by_az_id]
}
run "reject_attachment_owner_mismatch" {
  command = plan
  variables {
    shared_account_ids = ["777788889999"]
    spoke_attachments = {
      alb = {
        account_id      = "777788889999"
        role            = "ALB"
        vpc_cidrs       = ["10.10.0.0/16"]
        attachment_id   = "tgw-attach-00000000000000011"
        acceptance_mode = "existing"
        reported_state  = "available"
        az_ids          = ["apne2-az1", "apne2-az2"]
      }
    }
  }
  expect_failures = [data.aws_ec2_transit_gateway_vpc_attachment.spoke]
}

run "reject_duplicate_customer_gateway" {
  command = plan
  variables {
    vpn_networks = { branch = { cidrs = ["10.30.0.0/16"], kind = "onprem" } }
    vpn_peers = {
      first = {
        network_key         = "branch"
        public_ip           = "1.1.1.1"
        bgp_asn             = 65001
        tunnel1_inside_cidr = "169.254.10.0/30"
        tunnel2_inside_cidr = "169.254.10.4/30"
      }
      second = {
        network_key         = "branch"
        public_ip           = "1.1.1.1"
        bgp_asn             = 65001
        tunnel1_inside_cidr = "169.254.10.8/30"
        tunnel2_inside_cidr = "169.254.10.12/30"
      }
    }
  }
  expect_failures = [var.vpn_peers]
}

run "client_spoke" {
  command = plan
  variables {
    shared_account_ids = ["444455556666"]
    spoke_attachments = {
      aws_client = {
        account_id      = "444455556666"
        role            = "Client"
        vpc_cidrs       = ["10.10.0.0/16"]
        attachment_id   = "tgw-attach-00000000000000011"
        acceptance_mode = "accept"
        reported_state  = "pendingAcceptance"
        az_ids          = ["apne2-az1", "apne2-az2"]
      }
    }
  }
  assert {
    condition     = contains(keys(aws_ec2_transit_gateway_vpc_attachment_accepter.spoke), "aws_client") && length(aws_ec2_transit_gateway_route.to_spoke) == 1 && contains(local.home_net, "10.10.0.0/16") && alltrue([for route in aws_ec2_transit_gateway_route.before_inspection : route.transit_gateway_attachment_id == aws_ec2_transit_gateway_vpc_attachment.inspection.id])
    error_message = "Client spokes must receive central acceptance, return routing and inspection without a bypass."
  }
}

run "internal_alb_two_port_demo" {
  command = plan
  variables {
    shared_account_ids = ["444455556666"]
    spoke_attachments = {
      aws_client = {
        account_id    = "444455556666", role = "Client", vpc_cidrs = ["10.10.0.0/16"]
        attachment_id = "tgw-attach-00000000000000011", acceptance_mode = "accept", reported_state = "available"
        az_ids        = ["apne2-az1", "apne2-az2"]
      }
      aws_server = {
        account_id    = "444455556666", role = "Backend", vpc_cidrs = ["10.20.0.0/16"]
        attachment_id = "tgw-attach-00000000000000012", acceptance_mode = "accept", reported_state = "pendingAcceptance"
        az_ids        = ["apne2-az1", "apne2-az2"]
      }
    }
    allowed_flows = {
      aws_client_to_server = {
        source_cidrs = ["10.10.4.0/24", "10.10.5.0/24"], destination_cidrs = ["10.20.1.10/32"]
        protocol     = "tcp", destination_ports = [8080], sid = 1000002
      }
    }
    suricata_rules = {
      http_test_marker = {
        sid  = 2000001
        rule = "alert http $HOME_NET any -> $EXTERNAL_NET any (msg:\"Delta HTTP test marker\"; flow:established,to_server; http.uri; content:\"/delta-test-marker\"; sid:2000001; rev:1;)"
      }
    }
  }
  assert {
    condition     = length(aws_ec2_transit_gateway_route.before_inspection) == 2 && alltrue([for route in aws_ec2_transit_gateway_route.before_inspection : route.transit_gateway_attachment_id == aws_ec2_transit_gateway_vpc_attachment.inspection.id]) && length(aws_ec2_transit_gateway_route.to_spoke) == 2 && length(aws_route.to_firewall) == 4 && length(aws_route.to_tgw) == 4 && length(aws_vpn_connection.peer) == 0
    error_message = "Both same-account VPCs must traverse inspection with explicit return routes and no VPN."
  }
  assert {
    condition     = alltrue([for association in aws_ec2_transit_gateway_route_table_association.spoke : association.transit_gateway_route_table_id == aws_ec2_transit_gateway_route_table.spoke.id]) && length(aws_ec2_transit_gateway_route_table_association.spoke) == 2 && length(aws_ec2_transit_gateway_route_table_propagation.vpn) == 0
    error_message = "Both source VPCs must associate with Spoke without direct propagation."
  }
  assert {
    condition     = contains(local.home_net, "10.10.0.0/16") && contains(local.home_net, "10.20.0.0/16") && strcontains(local.rules_string, "[10.20.1.10/32] [8080]") && !strcontains(local.rules_string, "8081") && strcontains(local.rules_string, "sid:2000001;") && endswith(local.rules_string, "sid:9000000; rev:1;)")
    error_message = "Allow only the 8080 control, keep the observation alert and final deny, and inspect both VPC CIDRs."
  }
}
