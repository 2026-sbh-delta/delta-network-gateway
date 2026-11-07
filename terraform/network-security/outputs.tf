output "central_account_id" {
  description = "Verified central AWS account ID."
  value       = data.aws_caller_identity.current.account_id
}

output "guardduty" {
  description = "Account/Region detector identity and managed protection states; no finding contents or credentials."
  value = {
    account_id                   = data.aws_caller_identity.current.account_id
    region                       = var.region
    detector_id                  = aws_guardduty_detector.account.id
    arn                          = aws_guardduty_detector.account.arn
    enabled                      = aws_guardduty_detector.account.enable
    finding_publishing_frequency = aws_guardduty_detector.account.finding_publishing_frequency
    features = { for name, feature in aws_guardduty_detector_feature.protection : name => {
      status                   = feature.status
      additional_configuration = { for config in feature.additional_configuration : config.name => config.status }
    } }
  }
}

output "guardduty_findings" {
  description = "Enterprise finding log storage and platform forwarding configuration; no finding contents."
  value = {
    log_group_name           = aws_cloudwatch_log_group.guardduty_findings.name
    log_group_arn            = aws_cloudwatch_log_group.guardduty_findings.arn
    event_rule_name          = aws_cloudwatch_event_rule.guardduty_findings.name
    event_rule_arn           = aws_cloudwatch_event_rule.guardduty_findings.arn
    destination_arn          = var.guardduty_findings_destination_arn
    forwarding_enabled       = var.guardduty_findings_forwarding_enabled
    subscription_filter_name = try(aws_cloudwatch_log_subscription_filter.guardduty_platform[0].name, null)
  }
}

output "network_firewall_logs" {
  description = "Enterprise Network Firewall FLOW/ALERT storage and platform forwarding configuration; no log contents."
  value = {
    log_groups         = { for type, group in aws_cloudwatch_log_group.firewall_enterprise : type => group.name }
    destination_arn    = var.network_firewall_logs_destination_arn
    forwarding_enabled = var.network_firewall_logs_forwarding_enabled
    subscription_filter_names = {
      for type in keys(aws_cloudwatch_log_group.firewall_enterprise) :
      type => try(aws_cloudwatch_log_subscription_filter.network_firewall_platform[type].name, null)
    }
  }
}

output "region" {
  description = "Region of the central deployment."
  value       = var.region
}

output "az_ids" {
  description = "Physical AZ IDs available to primary and cross-account secondary endpoints."
  value       = sort(keys(var.inspection_azs))
}

output "tgw_id" {
  description = "Shared central Transit Gateway ID."
  value       = aws_ec2_transit_gateway.central.id
}

output "tgw_arn" {
  description = "Shared central Transit Gateway ARN."
  value       = aws_ec2_transit_gateway.central.arn
}

output "spoke_route_table_id" {
  description = "TGW Spoke route table; sends registered VPC traffic to inspection or directly according to inspection_mode."
  value       = aws_ec2_transit_gateway_route_table.spoke.id
}

output "inspected_route_table_id" {
  description = "After-inspection TGW route table; only the Inspection VPC is associated."
  value       = aws_ec2_transit_gateway_route_table.inspected.id
}

output "inspection_attachment_id" {
  description = "Appliance-mode Inspection VPC attachment."
  value       = aws_ec2_transit_gateway_vpc_attachment.inspection.id
}

output "firewall_arn" {
  description = "Firewall ARN shared through RAM, or null in disabled mode."
  value       = try(aws_networkfirewall_firewall.central[0].arn, null)
}

output "inspection_mode" {
  description = "Traffic inspection mode: enabled, bypass (firewall retained), or disabled (endpoints removed)."
  value       = var.inspection_mode
}

output "inspection_route_targets" {
  description = "Desired static Spoke RT next hops. In direct modes VPN destinations use BGP propagation and are excluded from this map."
  value       = { for cidr, route in aws_ec2_transit_gateway_route.before_inspection : cidr => route.transit_gateway_attachment_id }
}

output "primary_endpoint_ids_by_az_id" {
  description = "Inspection VPC primary endpoint IDs by physical AZ ID, or an empty map in disabled mode."
  value       = local.primary_endpoint_ids
  precondition {
    condition     = alltrue([for endpoint_id in values(local.primary_endpoint_ids) : endpoint_id != null]) && length(distinct(values(local.primary_endpoint_ids))) == (local.firewall_enabled ? length(var.inspection_azs) : 0)
    error_message = "Each configured physical AZ requires exactly one primary Firewall endpoint."
  }
}

output "tgw_ram_share_arn" {
  description = "TGW RAM resource share ARN."
  value       = aws_ram_resource_share.central["tgw"].arn
}

output "firewall_ram_share_arn" {
  description = "Firewall RAM resource share ARN."
  value       = aws_ram_resource_share.central["firewall"].arn
}

output "log_groups" {
  description = "Legacy Network Firewall FLOW/ALERT CloudWatch Logs group names retained for historical logs."
  value       = { for type, group in aws_cloudwatch_log_group.firewall : type => group.name }
}

output "inspection_route_table_ids" {
  description = "Inspection VPC subnet route tables by physical AZ ID for route auditing."
  value = { for az_id in keys(var.inspection_azs) : az_id => {
    tgw      = aws_route_table.tgw[az_id].id
    firewall = aws_route_table.firewall[az_id].id
  } }
}

output "vpn_handoff" {
  description = "Nonsecret remote configuration contract. Excludes PSKs and customer_gateway_configuration."
  value = { for key, vpn in aws_vpn_connection.peer : key => {
    network_key            = var.vpn_peers[key].network_key
    remote_cidrs           = sort(tolist(var.vpn_networks[var.vpn_peers[key].network_key].cidrs))
    aws_cidrs_to_advertise = sort(flatten([for spoke in values(var.spoke_attachments) : tolist(spoke.vpc_cidrs)]))
    gcp_interface          = var.vpn_peers[key].gcp_interface
    customer_gateway_id    = aws_customer_gateway.peer[key].id
    vpn_connection_id      = vpn.id
    attachment_id          = vpn.transit_gateway_attachment_id
    customer_gateway_ip    = var.vpn_peers[key].public_ip
    customer_bgp_asn       = var.vpn_peers[key].bgp_asn
    ike_version            = "ikev2"
    encryption             = "AES256"
    integrity              = "SHA2-256"
    dh_group               = 14
    tunnels = {
      tunnel1 = {
        outside_ip     = vpn.tunnel1_address
        aws_inside_ip  = vpn.tunnel1_vgw_inside_address
        peer_inside_ip = vpn.tunnel1_cgw_inside_address
        aws_bgp_asn    = vpn.tunnel1_bgp_asn
        inside_cidr    = var.vpn_peers[key].tunnel1_inside_cidr
      }
      tunnel2 = {
        outside_ip     = vpn.tunnel2_address
        aws_inside_ip  = vpn.tunnel2_vgw_inside_address
        peer_inside_ip = vpn.tunnel2_cgw_inside_address
        aws_bgp_asn    = vpn.tunnel2_bgp_asn
        inside_cidr    = var.vpn_peers[key].tunnel2_inside_cidr
      }
    }
  } }
}

output "cidr_service_map" {
  description = "Metadata contract for later CSP/service_id enrichment; not raw Firewall log fields."
  value       = var.cidr_service_map
}
