variable "name" {
  description = "Resource name prefix."
  type        = string
  default     = "delta"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,19}$", var.name))
    error_message = "name must be a lowercase resource prefix of at most 20 characters."
  }
}

variable "vpc_name" {
  description = "VPC Name tag, independent of the shared resource name prefix."
  type        = string
  default     = "delta-board-vpc"
}

variable "environment" {
  description = "Environment name and tag."
  type        = string
  default     = "lab"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,15}$", var.environment))
    error_message = "environment must be a lowercase name of at most 16 characters."
  }
}

variable "region" {
  description = "Region shared with the central TGW."
  type        = string
  default     = "ap-northeast-2"
}

variable "workload_account_id" {
  description = "Expected workload account; checked against STS and Provider credentials."
  type        = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.workload_account_id))
    error_message = "workload_account_id must be a 12-digit account ID."
  }
}

variable "central_account_id" {
  description = "Expected owner of the shared TGW."
  type        = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.central_account_id)) && var.central_account_id != var.workload_account_id
    error_message = "central_account_id must be a different 12-digit account ID."
  }
}

variable "vpc_cidr" {
  description = "Canonical private IPv4 /16 for the workload VPC."
  type        = string
  validation {
    condition     = try(cidrnetmask(var.vpc_cidr) != "" && split("/", var.vpc_cidr)[1] == "16" && cidrhost(var.vpc_cidr, 0) == split("/", var.vpc_cidr)[0], false)
    error_message = "vpc_cidr must be a canonical IPv4 /16."
  }
}

variable "azs" {
  description = "Exactly two physical AZ IDs and dedicated private /24 TGW/client subnets."
  type = map(object({
    tgw_subnet_cidr    = string
    client_subnet_cidr = string
  }))
  validation {
    condition = length(var.azs) == 2 && alltrue(flatten([for az_id, az in var.azs : [
      can(regex("^[a-z0-9]+-az[0-9]+$", az_id)),
      contains(var.central_az_ids, az_id),
      alltrue([for cidr in [az.tgw_subnet_cidr, az.client_subnet_cidr] : try(
        cidrnetmask(cidr) != "" && split("/", cidr)[1] == "24" && cidrhost(cidr, 0) == split("/", cidr)[0], false
      )])
    ]]))
    error_message = "azs must contain exactly two central AZ IDs with canonical IPv4 /24 subnets."
  }
}

variable "central_az_ids" {
  description = "Physical AZ IDs supported by the central Inspection VPC."
  type        = set(string)
  validation {
    condition     = length(var.central_az_ids) >= 2 && alltrue([for id in var.central_az_ids : can(regex("^[a-z0-9]+-az[0-9]+$", id))])
    error_message = "central_az_ids must list at least two physical AZ IDs."
  }
}

variable "inspection_cidrs" {
  description = "Central Inspection VPC CIDRs to check for address overlap, not client routes."
  type        = set(string)
  validation {
    condition = length(var.inspection_cidrs) > 0 && alltrue([for cidr in var.inspection_cidrs : try(
      cidrnetmask(cidr) != "" && cidrhost(cidr, 0) == split("/", cidr)[0], false
    )])
    error_message = "inspection_cidrs must contain canonical IPv4 networks."
  }
}

variable "client_az_id" {
  description = "AZ for the retained management endpoint; legacy name preserves the existing input contract."
  type        = string
  validation {
    condition     = contains(keys(var.azs), var.client_az_id)
    error_message = "client_az_id must be a key in azs."
  }
}

variable "transit_gateway_id" {
  description = "Shared central TGW ID; null creates the standalone private foundation without an attachment."
  type        = string
  default     = null
  validation {
    condition     = var.transit_gateway_id == null ? true : can(regex("^tgw-[0-9a-f]{17}$", var.transit_gateway_id))
    error_message = "transit_gateway_id must be null or a full TGW ID."
  }
}

variable "remote_cidrs" {
  description = "Registered private remote networks routed to TGW after the destination and return path are ready."
  type        = set(string)
  default     = []
  validation {
    condition = alltrue([for cidr in var.remote_cidrs : try(
      cidrnetmask(cidr) != "" && cidrhost(cidr, 0) == split("/", cidr)[0], false
    )])
    error_message = "remote_cidrs must contain canonical IPv4 networks."
  }
  validation {
    condition     = length(var.remote_cidrs) == 0 || var.transit_gateway_id != null
    error_message = "Remote routes require an enabled shared TGW attachment."
  }
}

variable "test_tcp_ports" {
  description = "TCP egress ports for the retained workload security group to registered remote networks."
  type        = set(number)
  default     = [8080, 8081]
  validation {
    condition     = length(var.test_tcp_ports) > 0 && alltrue([for port in var.test_tcp_ports : floor(port) == port && port >= 1 && port <= 65535])
    error_message = "test_tcp_ports must contain integer TCP ports between 1 and 65535."
  }
}

variable "tags" {
  description = "Additional common tags; Name remains resource specific."
  type        = map(string)
  default     = {}
}

variable "guardduty_enabled" {
  description = "Enable account/Region threat detection; false suspends the retained detector without deleting findings."
  type        = bool
  default     = true
  nullable    = false
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
    S3_DATA_EVENTS         = { status = "ENABLED" }
    EKS_AUDIT_LOGS         = { status = "ENABLED" }
    EBS_MALWARE_PROTECTION = { status = "ENABLED" }
    RDS_LOGIN_EVENTS = {
      status                   = "ENABLED"
      additional_configuration = { RDS_DATA_RISK = "ENABLED" }
    }
    LAMBDA_NETWORK_LOGS = { status = "ENABLED" }
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

variable "application_enabled" {
  description = "Explicit opt-in for the public ALB, private x86 EC2 hosts and RDS application resources."
  type        = bool
  default     = false
}

variable "internet_inspection" {
  description = "Opt-in shared Network Firewall for this existing VPC only; bypass prepares endpoints without switching public routes."
  type = object({
    mode            = string
    firewall_arn    = string
    expected_vpc_id = string
    primary_az_ids  = set(string)
    subnet_cidrs    = map(string)
  })
  default = null
  validation {
    condition = var.internet_inspection == null ? true : (
      contains(["bypass", "enabled"], var.internet_inspection.mode) &&
      can(regex("^vpc-[0-9a-f]{17}$", var.internet_inspection.expected_vpc_id)) &&
      can(regex("^arn:aws:network-firewall:[a-z0-9-]+:[0-9]{12}:firewall/[a-zA-Z0-9_-]+$", var.internet_inspection.firewall_arn))
    )
    error_message = "Internet inspection requires bypass/enabled, a full expected VPC ID, and a shared firewall ARN."
  }
}

variable "application_remote_cidrs" {
  description = "Explicit remote service peers allowed to the app frontend; no SG change when empty."
  type        = set(string)
  default     = []
  validation {
    condition     = alltrue([for cidr in var.application_remote_cidrs : try(cidrnetmask(cidr) != "" && cidrhost(cidr, 0) == split("/", cidr)[0] && cidr != "0.0.0.0/0", false)])
    error_message = "Application peers must be canonical, non-default IPv4 CIDRs."
  }
}

variable "application_remote_ports" {
  description = "Explicit remote frontend TCP service ports."
  type        = set(number)
  default     = [3000]
  validation {
    condition     = length(var.application_remote_ports) > 0 && alltrue([for port in var.application_remote_ports : floor(port) == port && port >= 1 && port <= 65535])
    error_message = "Remote application TCP ports must be integers in 1..65535."
  }
}

variable "application" {
  description = "Optional sample app configuration. Existing network resources and backend remain unchanged."
  type = object({
    deployment_mode       = optional(string, "native")
    retain_legacy_backend = optional(bool, false)
    public_subnet_cidrs   = optional(map(string), { apne2-az1 = "10.10.10.0/24", apne2-az2 = "10.10.11.0/24" })
    db_subnet_cidrs       = optional(map(string), { apne2-az1 = "10.10.20.0/24", apne2-az2 = "10.10.21.0/24" })
    ami_id                = optional(string)
    instance_type         = optional(string, "t3.small")
    db_engine_version     = optional(string, "18.3")
    db_instance_class     = optional(string, "db.t3.micro")
    db_allocated_storage  = optional(number, 20)
    certificate_arn       = optional(string)
    hostname              = optional(string)
    node_version          = optional(string, "24.19.0")
    node_sha256           = optional(string, "14b342e71204f811bde6153be8e04b62aef63c236fef92b55f9c83154b409647")
  })
  default = {}
  validation {
    condition     = contains(["native", "compose"], var.application.deployment_mode) && (!var.application.retain_legacy_backend || var.application.deployment_mode == "compose")
    error_message = "Choose native or compose; retain_legacy_backend is only valid during a Compose migration."
  }
  validation {
    condition     = can(regex("^24[.][0-9]+[.][0-9]+$", var.application.node_version)) && can(regex("^[a-f0-9]{64}$", var.application.node_sha256)) && var.application.db_allocated_storage >= 20 && floor(var.application.db_allocated_storage) == var.application.db_allocated_storage && can(regex("^[0-9]+[.][0-9]+$", var.application.db_engine_version))
    error_message = "Use a pinned Node.js 24 version and SHA-256, integer DB storage >=20 GiB and a major.minor PostgreSQL version."
  }
  validation {
    condition     = (var.application.certificate_arn == null || try(can(regex("^[a-zA-Z0-9.-]+[.][a-zA-Z]{2,}$", var.application.hostname)), false)) && can(regex("^db[.](t3|t3a|m[0-9]+[id]*|r[0-9]+[id]*)[.]", var.application.db_instance_class))
    error_message = "HTTPS requires the certificate's hostname; the RDS class must use an x86 family."
  }

}
