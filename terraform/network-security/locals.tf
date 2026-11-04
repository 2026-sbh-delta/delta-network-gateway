locals {
  prefix           = "${var.name}-${var.environment}"
  firewall_enabled = var.inspection_mode != "disabled"
  common_tags = merge({
    Team        = "Delta"
    Project     = "delta-multicloud"
    Environment = var.environment
    ManagedBy   = "Terraform"
  }, var.tags)

  subnets = merge([for az_id, az in var.inspection_azs : {
    "${az_id}/tgw"      = { az_id = az_id, cidr = az.tgw_subnet_cidr, role = "tgw" }
    "${az_id}/firewall" = { az_id = az_id, cidr = az.firewall_subnet_cidr, role = "firewall" }
  }]...)

  remote_networks = concat(
    flatten([for key, spoke in var.spoke_attachments : [for cidr in spoke.vpc_cidrs : { owner = "spoke/${key}", cidr = cidr }]]),
    flatten([for key, network in var.vpn_networks : [for cidr in network.cidrs : { owner = "vpn/${key}", cidr = cidr }]])
  )
  inspected_cidrs = toset([for network in local.remote_networks : network.cidr])
  # Direct VPN routes come from BGP, preserving both HA attachments and withdrawals.
  # A static route to one VPN attachment would break ECMP and failover.
  spoke_destination_cidrs = toset(flatten([for spoke in values(var.spoke_attachments) : tolist(spoke.vpc_cidrs)]))
  static_spoke_cidrs      = var.inspection_mode == "enabled" ? local.inspected_cidrs : local.spoke_destination_cidrs
  home_net                = sort(tolist(setunion(toset([var.inspection_vpc_cidr]), local.inspected_cidrs)))

  # Integer IPv4 intervals permit complete containment/overlap checks, including
  # differently sized prefixes. Invalid CIDRs are rejected before any AWS writes.
  network_ranges = [for network in concat([{ owner = "inspection", cidr = var.inspection_vpc_cidr }], local.remote_networks) : {
    owner = network.owner
    cidr  = network.cidr
    start = try(sum([for index, octet in split(".", cidrhost(network.cidr, 0)) : tonumber(octet) * pow(256, 3 - index)]), -1)
    end   = try(sum([for index, octet in split(".", cidrhost(network.cidr, -1)) : tonumber(octet) * pow(256, 3 - index)]), -1)
  }]
  subnet_ranges = [for key, subnet in local.subnets : {
    key   = key
    start = try(sum([for index, octet in split(".", cidrhost(subnet.cidr, 0)) : tonumber(octet) * pow(256, 3 - index)]), -1)
    end   = try(sum([for index, octet in split(".", cidrhost(subnet.cidr, -1)) : tonumber(octet) * pow(256, 3 - index)]), -1)
  }]
  inspection_range = one([for network in local.network_ranges : network if network.owner == "inspection"])
  route_matrix = { for entry in flatten([for az_id in keys(var.inspection_azs) : [for cidr in local.inspected_cidrs : {
    key = "${az_id}/${cidr}", az_id = az_id, cidr = cidr
  }]]) : entry.key => entry }
  spoke_routes = { for entry in flatten([for key, spoke in var.spoke_attachments : [for cidr in spoke.vpc_cidrs : {
    key = "${key}/${cidr}", spoke_key = key, cidr = cidr
  }]]) : entry.key => entry }
  direct_spoke_targets = merge([for spoke in values(var.spoke_attachments) : {
    for cidr in spoke.vpc_cidrs : cidr => spoke.attachment_id
  }]...)

  input_cidrs = concat(
    [var.inspection_vpc_cidr], [for subnet in values(local.subnets) : subnet.cidr],
    [for network in local.remote_networks : network.cidr],
    flatten([for flow in values(var.allowed_flows) : concat(tolist(flow.source_cidrs), tolist(flow.destination_cidrs))]),
    [for service in values(var.cidr_service_map) : service.cidr]
  )

  # IANA non-global IPv4 networks. IPv4 integer ranges avoid textual prefix tests.
  nonglobal_cidrs = [
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
    "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24", "192.168.0.0/16", "198.18.0.0/15",
    "198.51.100.0/24", "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4"
  ]
  nonglobal_ranges = [for cidr in local.nonglobal_cidrs : {
    start = sum([for index, octet in split(".", cidrhost(cidr, 0)) : tonumber(octet) * pow(256, 3 - index)])
    end   = sum([for index, octet in split(".", cidrhost(cidr, -1)) : tonumber(octet) * pow(256, 3 - index)])
  }]
  peer_ip_numbers = { for key, peer in var.vpn_peers : key => try(sum([for index, octet in split(".", peer.public_ip) : tonumber(octet) * pow(256, 3 - index)]), -1) }

  # Set a connection bit without pass: later payload signatures keep evaluating.
  # Only the initiating direction may acquire this bit. Replies share the flow.
  flow_rules = [for key in sort(keys(var.allowed_flows)) : format(
    "alert %s [%s] any -> [%s] %s (msg:\"Delta allow SID %s\"; flow:to_server; %sflowbits:set,delta_allowed; flowbits:noalert; sid:%s; rev:1;)",
    var.allowed_flows[key].protocol,
    join(",", sort(tolist(var.allowed_flows[key].source_cidrs))),
    join(",", sort(tolist(var.allowed_flows[key].destination_cidrs))),
    var.allowed_flows[key].protocol == "icmp" ? "any" : "[${join(",", [for port in sort([for port in var.allowed_flows[key].destination_ports : tostring(port)]) : port])}]",
    var.allowed_flows[key].sid,
    var.allowed_flows[key].protocol == "icmp" ? "itype:${var.allowed_flows[key].icmp_type}; ${var.allowed_flows[key].icmp_code == null ? "" : "icode:${var.allowed_flows[key].icmp_code}; "}" : "",
    var.allowed_flows[key].sid
  )]
  deny_sid = 9000000
  rules_string = join("\n", concat(
    [for key in sort(keys(var.suricata_rules)) : var.suricata_rules[key].rule],
    local.flow_rules,
    ["drop ip any any -> any any (msg:\"Delta default deny\"; flowbits:isnotset,delta_allowed; sid:${local.deny_sid}; rev:1;)"]
  ))
}

resource "terraform_data" "contract" {
  input = "delta-central-contract-v1"
  lifecycle {
    precondition {
      condition = alltrue([for cidr in local.input_cidrs : try(
        cidrnetmask(cidr) != "" && cidrhost(cidr, 0) == split("/", cidr)[0], false
      )])
      error_message = "All network, flow and service metadata CIDRs must be canonical IPv4 networks."
    }
    precondition {
      condition     = alltrue([for network in local.remote_networks : network.cidr != "0.0.0.0/0"])
      error_message = "Inspection routes must not include an internet default route."
    }
    precondition {
      condition = alltrue(flatten([for i, first in local.network_ranges : [for j, second in local.network_ranges :
        !(first.start <= second.end && second.start <= first.end) if i < j
      ]]))
      error_message = "Inspection, spoke and logical VPN networks must not overlap or duplicate. Define HA VPN CIDRs once in vpn_networks."
    }
    precondition {
      condition = alltrue([for subnet in local.subnet_ranges :
        subnet.start >= local.inspection_range.start && subnet.end <= local.inspection_range.end
        ]) && alltrue(flatten([for i, first in local.subnet_ranges : [for j, second in local.subnet_ranges :
          !(first.start <= second.end && second.start <= first.end) if i < j
      ]]))
      error_message = "All dedicated subnets must be disjoint and contained in the Inspection VPC."
    }
    precondition {
      condition = alltrue([for key, ip in local.peer_ip_numbers : alltrue([for reserved in local.nonglobal_ranges :
        !(ip >= reserved.start && ip <= reserved.end)
      ])])
      error_message = "Customer gateways need globally routable public IPv4 addresses; private, documentation and reserved addresses cannot create VPNs."
    }
    precondition {
      condition = alltrue([for key, network in var.vpn_networks : network.kind == "gcp" ? (
        length([for peer in values(var.vpn_peers) : peer if peer.network_key == key]) == 2 &&
        toset([for peer in values(var.vpn_peers) : peer.gcp_interface if peer.network_key == key]) == toset([0, 1]) &&
        length(distinct([for peer in values(var.vpn_peers) : peer.public_ip if peer.network_key == key])) == 2 &&
        length(distinct([for peer in values(var.vpn_peers) : peer.bgp_asn if peer.network_key == key])) == 1
        ) : length([for peer in values(var.vpn_peers) : peer if peer.network_key == key]) > 0
      ])
      error_message = "Each remote network needs a peer. GCP HA requires exactly two distinct gateway IPs, interfaces 0/1 and one shared Cloud Router ASN."
    }
    precondition {
      condition     = data.aws_caller_identity.current.account_id == var.central_account_id
      error_message = "Authenticated AWS account differs from the explicit central_account_id."
    }
    precondition {
      condition = alltrue([for az_id, az in data.aws_availability_zone.selected :
        az.zone_id == az_id && az.region == var.region && az.zone_type == "availability-zone"
      ])
      error_message = "Every AZ ID must be an available standard AZ in the selected Region."
    }
  }
}
