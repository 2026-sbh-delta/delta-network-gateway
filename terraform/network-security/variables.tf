variable "name" {
  description = "Resource name prefix."
  type        = string
  default     = "delta"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,25}$", var.name))
    error_message = "name must be a lowercase resource prefix of at most 26 characters."
  }
}

variable "environment" {
  description = "Environment name used in names and tags."
  type        = string
  default     = "lab"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,15}$", var.environment))
    error_message = "environment must be a lowercase name of at most 16 characters."
  }
}

variable "region" {
  description = "AWS Region for all central resources."
  type        = string
  default     = "ap-northeast-2"
}

variable "central_account_id" {
  description = "Expected Network/Security AWS account ID; never inferred from the current credentials."
  type        = string
  nullable    = false
  validation {
    condition     = can(regex("^[0-9]{12}$", var.central_account_id))
    error_message = "central_account_id must contain exactly 12 digits."
  }
}

variable "inspection_vpc_cidr" {
  description = "Canonical IPv4 Inspection VPC network, /16 through /28."
  type        = string
  nullable    = false
  validation {
    condition = try(
      cidrnetmask(var.inspection_vpc_cidr) != "" &&
      cidrhost(var.inspection_vpc_cidr, 0) == split("/", var.inspection_vpc_cidr)[0] &&
      tonumber(split("/", var.inspection_vpc_cidr)[1]) >= 16 &&
      tonumber(split("/", var.inspection_vpc_cidr)[1]) <= 28, false
    )
    error_message = "inspection_vpc_cidr must be a canonical IPv4 /16 through /28 network."
  }
}

variable "inspection_azs" {
  description = "Physical AZ ID => dedicated TGW and Firewall subnet CIDRs; the example uses two AZs."
  type = map(object({
    tgw_subnet_cidr      = string
    firewall_subnet_cidr = string
  }))
  nullable = false
  validation {
    condition     = length(var.inspection_azs) >= 2 && alltrue([for az_id in keys(var.inspection_azs) : can(regex("^[a-z0-9]+-az[0-9]+$", az_id))])
    error_message = "inspection_azs requires at least two physical AZ IDs, not account-specific AZ names."
  }
  validation {
    condition = alltrue(flatten([for az in values(var.inspection_azs) : [for subnet_cidr in [az.tgw_subnet_cidr, az.firewall_subnet_cidr] : try(
      cidrnetmask(subnet_cidr) != "" && cidrhost(subnet_cidr, 0) == split("/", subnet_cidr)[0] &&
      tonumber(split("/", subnet_cidr)[1]) >= 16 && tonumber(split("/", subnet_cidr)[1]) <= 28, false
    )]]))
    error_message = "All dedicated subnets must be canonical IPv4 networks of /28 or larger."
  }
}

variable "tgw_asn" {
  description = "Private BGP ASN of the TGW; must differ from each customer gateway ASN."
  type        = number
  default     = 64512
  validation {
    condition     = floor(var.tgw_asn) == var.tgw_asn && ((var.tgw_asn >= 64512 && var.tgw_asn <= 65534) || (var.tgw_asn >= 4200000000 && var.tgw_asn <= 4294967294))
    error_message = "tgw_asn must be a private 16-bit or 32-bit ASN."
  }
}

variable "tags" {
  description = "Overrides for common Team, Project, Environment and ManagedBy tags; Name is resource-specific."
  type        = map(string)
  default     = {}
}

variable "shared_account_ids" {
  description = "Consumer account IDs for both RAM shares."
  type        = set(string)
  default     = []
  validation {
    condition     = alltrue([for account_id in var.shared_account_ids : can(regex("^[0-9]{12}$", account_id)) && account_id != var.central_account_id])
    error_message = "Share principals must be 12-digit consumer accounts distinct from the central account."
  }
}

variable "allow_external_principals" {
  description = "Allow sharing outside the AWS Organization. Organization integration is never modified here."
  type        = bool
  default     = false
}

variable "spoke_attachments" {
  description = "Stable connection key => consumer connection contract. accept/manage mode remains unchanged after acceptance."
  type = map(object({
    account_id      = string
    role            = string
    vpc_cidrs       = set(string)
    attachment_id   = string
    acceptance_mode = string
    reported_state  = string
    az_ids          = set(string)
  }))
  default = {}
  validation {
    condition = alltrue([for spoke in values(var.spoke_attachments) :
      can(regex("^[0-9]{12}$", spoke.account_id)) && contains(var.shared_account_ids, spoke.account_id) &&
      contains(["ALB", "Backend", "Client"], spoke.role) && can(regex("^tgw-attach-[0-9a-f]+$", spoke.attachment_id)) &&
      contains(["accept", "existing"], spoke.acceptance_mode) &&
      contains(["pendingAcceptance", "available"], spoke.reported_state) &&
      (spoke.acceptance_mode != "existing" || spoke.reported_state == "available") &&
      length(spoke.vpc_cidrs) > 0 && length(spoke.az_ids) > 0 &&
      length(setsubtract(spoke.az_ids, toset(keys(var.inspection_azs)))) == 0
    ])
    error_message = "Spokes require a shared account, ALB/Backend/Client role, valid attachment, accept/existing mode, valid reported state and supported AZ IDs."
  }
  validation {
    condition     = length(distinct([for spoke in values(var.spoke_attachments) : spoke.attachment_id])) == length(var.spoke_attachments)
    error_message = "One attachment ID must not be managed under multiple connection keys."
  }
}

variable "vpn_networks" {
  description = "Logical remote networks, defined once even when a GCP HA gateway uses two AWS VPN connections."
  type = map(object({
    cidrs = set(string)
    kind  = string
  }))
  default = {}
  validation {
    condition     = alltrue([for network in values(var.vpn_networks) : length(network.cidrs) > 0 && contains(["gcp", "onprem"], network.kind)])
    error_message = "Each VPN network must have CIDRs and kind gcp or onprem."
  }
}

variable "vpn_peers" {
  description = "AWS-side BGP VPN connections; no VPN or CGW is created when empty."
  type = map(object({
    network_key         = string
    public_ip           = string
    bgp_asn             = number
    tunnel1_inside_cidr = string
    tunnel2_inside_cidr = string
    gcp_interface       = optional(number)
  }))
  default = {}
  validation {
    condition = alltrue([for peer in values(var.vpn_peers) :
      contains(keys(var.vpn_networks), peer.network_key) &&
      floor(peer.bgp_asn) == peer.bgp_asn && peer.bgp_asn != var.tgw_asn &&
      ((peer.bgp_asn >= 64512 && peer.bgp_asn <= 65534) || (peer.bgp_asn >= 4200000000 && peer.bgp_asn <= 4294967294)) &&
      (peer.gcp_interface == null ? true : contains([0, 1], peer.gcp_interface))
    ])
    error_message = "VPN peers must reference a defined remote network and use a distinct private BGP ASN and optional GCP interface 0/1."
  }
  validation {
    condition = alltrue([for peer in values(var.vpn_peers) : try(
      cidrnetmask("${peer.public_ip}/32") != "" && cidrhost("${peer.public_ip}/32", 0) == peer.public_ip, false
    )])
    error_message = "Customer gateway public_ip must be a valid IPv4 address."
  }
  validation {
    condition = alltrue(flatten([for peer in values(var.vpn_peers) : [for inside_cidr in [peer.tunnel1_inside_cidr, peer.tunnel2_inside_cidr] : try(
      cidrnetmask(inside_cidr) != "" && split("/", inside_cidr)[1] == "30" &&
      cidrhost(inside_cidr, 0) == split("/", inside_cidr)[0] &&
      startswith(inside_cidr, "169.254.") &&
      !contains(["169.254.0.0/30", "169.254.1.0/30", "169.254.2.0/30", "169.254.3.0/30", "169.254.4.0/30", "169.254.5.0/30", "169.254.169.252/30"], inside_cidr), false
    )]]))
    error_message = "Tunnel inside CIDRs must be canonical, non-reserved /30 networks within 169.254.0.0/16."
  }
  validation {
    condition     = length(distinct(flatten([for peer in values(var.vpn_peers) : [peer.tunnel1_inside_cidr, peer.tunnel2_inside_cidr]]))) == 2 * length(var.vpn_peers)
    error_message = "Tunnel inside CIDRs must be unique across all connections on this TGW."
  }
  validation {
    condition     = length(distinct([for peer in values(var.vpn_peers) : jsonencode([peer.public_ip, peer.bgp_asn])])) == length(var.vpn_peers)
    error_message = "Each gateway IP/ASN pair must have one peer key to avoid managing the same Customer Gateway twice."
  }
}

variable "allowed_flows" {
  description = "Explicit initiated flows. TCP/UDP require ports. ICMP requires a request type and optional code. SID is stable and caller-managed."
  type = map(object({
    source_cidrs      = set(string)
    destination_cidrs = set(string)
    protocol          = string
    destination_ports = optional(set(number), [])
    icmp_type         = optional(number)
    icmp_code         = optional(number)
    sid               = number
  }))
  default = {}
  validation {
    condition = alltrue([for flow in values(var.allowed_flows) :
      contains(["tcp", "udp", "icmp"], flow.protocol) && length(flow.source_cidrs) > 0 && length(flow.destination_cidrs) > 0 &&
      floor(flow.sid) == flow.sid && flow.sid >= 1000000 && flow.sid < 2000000 &&
      (flow.protocol == "icmp" ? (
        length(flow.destination_ports) == 0 && try(flow.icmp_type >= 0 && flow.icmp_type <= 255 && floor(flow.icmp_type) == flow.icmp_type, false) &&
        (flow.icmp_code == null ? true : flow.icmp_code >= 0 && flow.icmp_code <= 255 && floor(flow.icmp_code) == flow.icmp_code)
        ) : (
        flow.icmp_type == null && flow.icmp_code == null && length(flow.destination_ports) > 0 &&
        alltrue([for port in flow.destination_ports : port >= 1 && port <= 65535 && floor(port) == port])
      ))
    ])
    error_message = "Flows require tcp/udp destination ports or ICMP type/code, nonempty CIDRs and a unique integer SID in [1000000, 2000000)."
  }
  validation {
    condition     = length(distinct([for flow in values(var.allowed_flows) : flow.sid])) == length(var.allowed_flows)
    error_message = "Flow SIDs must be unique."
  }
}

variable "suricata_rules" {
  description = "Optional extra drop/alert/reject rules, keyed by stable name. Full rule strings include their declared SID; pass and flowbit modification are forbidden. No signatures bundled."
  type = map(object({
    sid  = number
    rule = string
  }))
  default = {}
  validation {
    condition = alltrue([for rule in values(var.suricata_rules) :
      floor(rule.sid) == rule.sid && rule.sid >= 2000000 && rule.sid < 9000000 &&
      can(regex("^(drop|alert|reject) [^\r\n]+$", rule.rule)) &&
      length(regexall("sid\\s*:", rule.rule)) == 1 &&
      can(regex("sid\\s*:\\s*${rule.sid}\\s*;", rule.rule)) &&
      !can(regex("(?i)flowbits\\s*:\\s*(set|unset|toggle|reset)|\\bpriority\\s*:|\\bnoalert\\s*;", rule.rule))
    ])
    error_message = "Custom rules must be one drop/alert/reject rule, have the declared SID in [2000000, 9000000), and cannot modify flowbits or set priority/noalert."
  }
  validation {
    condition     = length(distinct([for rule in values(var.suricata_rules) : rule.sid])) == length(var.suricata_rules)
    error_message = "Custom rule SIDs must be unique."
  }
}

variable "stateful_rule_capacity" {
  description = "Immutable rule-group capacity. Reserves room for flow rules, custom signatures and the final deny rule."
  type        = number
  default     = 1000
  validation {
    condition     = floor(var.stateful_rule_capacity) == var.stateful_rule_capacity && var.stateful_rule_capacity >= length(var.allowed_flows) + length(var.suricata_rules) + 1 && var.stateful_rule_capacity <= 30000
    error_message = "Rule capacity must cover all generated/custom rules plus the deny rule and be no greater than 30000."
  }
}

variable "inspection_mode" {
  description = "enabled inspects traffic, bypass retains the firewall, disabled removes billable endpoints. Direct modes route VPCs statically and VPNs through propagated BGP routes."
  type        = string
  default     = "enabled"
  nullable    = false
  validation {
    condition     = contains(["enabled", "bypass", "disabled"], var.inspection_mode)
    error_message = "inspection_mode must be enabled, bypass or disabled."
  }
}

variable "firewall_protection" {
  description = "Deletion, subnet and policy-association protections; disable the required protection in a separate apply before teardown or topology changes."
  type = object({
    delete = optional(bool, true)
    subnet = optional(bool, true)
    policy = optional(bool, true)
  })
  default = {}
}

variable "log_retention_days" {
  description = "CloudWatch log retention, in days."
  type        = number
  default     = 30
  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653], var.log_retention_days)
    error_message = "Select a supported finite CloudWatch Logs retention period."
  }
}

variable "retain_logs_on_destroy" {
  description = "Retain CloudWatch log groups when their Terraform resources are destroyed (requires import before re-managing)."
  type        = bool
  default     = true
}

variable "network_firewall_logs_forwarding_enabled" {
  description = "Forward the enterprise Network Firewall FLOW and ALERT groups to the platform while retaining local storage."
  type        = bool
  default     = true
  nullable    = false
}

variable "network_firewall_logs_destination_arn" {
  description = "Same-Region CloudWatch Logs logical Destination for enterprise Network Firewall logs."
  type        = string
  default     = "arn:aws:logs:ap-northeast-2:616723161368:destination:delta-platform-logs"
  nullable    = false
  validation {
    condition     = can(regex("^arn:aws:logs:${var.region}:616723161368:destination:[A-Za-z0-9_.#/-]+$", var.network_firewall_logs_destination_arn))
    error_message = "Use a same-Region CloudWatch Logs logical Destination in platform account 616723161368."
  }
}

variable "guardduty_enabled" {
  description = "Enable account/Region threat detection; false suspends the retained detector without deleting findings."
  type        = bool
  default     = true
  nullable    = false
}

variable "guardduty_findings_forwarding_enabled" {
  description = "Forward the enterprise GuardDuty log group to the platform; false retains local finding storage and detection."
  type        = bool
  default     = true
  nullable    = false
}

variable "guardduty_findings_destination_arn" {
  description = "Same-Region CloudWatch Logs logical Destination in the existing platform account."
  type        = string
  default     = "arn:aws:logs:ap-northeast-2:616723161368:destination:delta-platform-logs"
  nullable    = false
  validation {
    condition     = can(regex("^arn:aws:logs:${var.region}:616723161368:destination:[A-Za-z0-9_.#/-]+$", var.guardduty_findings_destination_arn))
    error_message = "Use a same-Region CloudWatch Logs logical Destination in platform account 616723161368."
  }
}

variable "guardduty_finding_publishing_frequency" {
  description = "Notification frequency for subsequent occurrences of existing GuardDuty findings."
  type        = string
  default     = "SIX_HOURS"
  nullable    = false
  validation {
    condition     = contains(["FIFTEEN_MINUTES", "ONE_HOUR", "SIX_HOURS"], var.guardduty_finding_publishing_frequency)
    error_message = "GuardDuty finding frequency must be FIFTEEN_MINUTES, ONE_HOUR or SIX_HOURS."
  }
}

variable "guardduty_features" {
  description = "Explicit optional protection states. Keep all required keys; removing a feature resource does not disable it in AWS."
  type = map(object({
    status                   = string
    additional_configuration = optional(map(string), {})
  }))
  nullable = false
  default = {
    S3_DATA_EVENTS         = { status = "DISABLED" }
    EKS_AUDIT_LOGS         = { status = "DISABLED" }
    EBS_MALWARE_PROTECTION = { status = "DISABLED" }
    RDS_LOGIN_EVENTS = {
      status                   = "DISABLED"
      additional_configuration = { RDS_DATA_RISK = "DISABLED" }
    }
    LAMBDA_NETWORK_LOGS = { status = "DISABLED" }
    RUNTIME_MONITORING = {
      status = "DISABLED"
      additional_configuration = {
        EKS_ADDON_MANAGEMENT         = "DISABLED"
        ECS_FARGATE_AGENT_MANAGEMENT = "DISABLED"
        EC2_AGENT_MANAGEMENT         = "DISABLED"
      }
    }
    AI_PROTECTION = { status = "DISABLED" }
  }
  validation {
    condition = alltrue([for name in [
      "S3_DATA_EVENTS", "EKS_AUDIT_LOGS", "EBS_MALWARE_PROTECTION",
      "RDS_LOGIN_EVENTS", "LAMBDA_NETWORK_LOGS", "RUNTIME_MONITORING", "AI_PROTECTION"
      ] : contains(keys(var.guardduty_features), name)]) && alltrue([
      for name in keys(var.guardduty_features) : contains([
        "S3_DATA_EVENTS", "EKS_AUDIT_LOGS", "EBS_MALWARE_PROTECTION", "RDS_LOGIN_EVENTS",
        "LAMBDA_NETWORK_LOGS", "RUNTIME_MONITORING", "AI_PROTECTION", "AI_ANALYST"
      ], name)
    ])
    error_message = "Keep all seven required feature keys; only supported AI_ANALYST may be added. Do not combine the legacy EKS runtime feature with RUNTIME_MONITORING."
  }
  validation {
    condition = try(alltrue([for feature in values(var.guardduty_features) :
      contains(["ENABLED", "DISABLED"], feature.status) &&
      alltrue([for status in values(feature.additional_configuration) : contains(["ENABLED", "DISABLED"], status)])
    ]), false)
    error_message = "Every GuardDuty feature and additional configuration must explicitly be ENABLED or DISABLED."
  }
  validation {
    condition = try(alltrue([for name, feature in var.guardduty_features :
      name == "RUNTIME_MONITORING" ? toset(keys(feature.additional_configuration)) == toset([
        "EKS_ADDON_MANAGEMENT", "ECS_FARGATE_AGENT_MANAGEMENT", "EC2_AGENT_MANAGEMENT"
        ]) : name == "RDS_LOGIN_EVENTS" ? toset(keys(feature.additional_configuration)) == toset([
        "RDS_DATA_RISK"
      ]) : length(feature.additional_configuration) == 0
    ]), false)
    error_message = "Runtime requires all three explicit agent-management settings; RDS requires RDS_DATA_RISK; other features cannot have additional configuration."
  }
  validation {
    condition = try(alltrue([for feature in values(var.guardduty_features) :
      feature.status == "ENABLED" || alltrue([for status in values(feature.additional_configuration) : status == "DISABLED"])
    ]), false)
    error_message = "A disabled protection cannot enable any of its additional configurations."
  }
}

variable "cidr_service_map" {
  description = "Optional metadata for later enrichment, not native Network Firewall log fields."
  type = map(object({
    cidr       = string
    csp        = string
    service_id = string
  }))
  default = {}
}
