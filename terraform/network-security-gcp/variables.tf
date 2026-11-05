variable "project_id" {
  type        = string
  default     = "term2-delta"
  description = "Approved teammate project; no other project is allowed."
  validation {
    condition     = var.project_id == "term2-delta"
    error_message = "Only the approved term2-delta project may be managed."
  }
}
variable "project_number" {
  type        = string
  description = "Expected Project Number, verified against the API."
  validation {
    condition     = can(regex("^[0-9]{6,20}$", var.project_number))
    error_message = "Supply the verified numeric Project Number."
  }
}
variable "name" {
  type        = string
  default     = "delta-lab"
  description = "Resource prefix."
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,24}$", var.name))
    error_message = "Use 3-25 lowercase characters, beginning with a letter."
  }
}
variable "region" {
  type        = string
  default     = "asia-northeast3"
  description = "GCP region."
}
variable "zone" {
  type        = string
  default     = "asia-northeast3-a"
  description = "Shared workload and firewall endpoint zone."
}
variable "subnets" {
  type        = object({ central = string, workload = string })
  description = "Separate central detection/connectivity and workload subnet CIDRs."
}
variable "application_subnets" {
  type        = map(string)
  default     = {}
  description = "Application backend, database PSC and ALB proxy-only subnet CIDRs in the existing workload VPC."
  validation {
    condition     = length(var.application_subnets) == 0 || toset(keys(var.application_subnets)) == toset(["backend", "database", "proxy"])
    error_message = "Supply all three backend, database and proxy subnets, or an empty map."
  }
}
variable "application_service_accounts" {
  type        = object({ frontend = string, backend = string })
  default     = null
  description = "Application VM service-account emails used to target firewall policy rules."
}
variable "aws_cidrs" {
  type        = set(string)
  description = "Approved AWS workload prefixes; NCC imports only these prefixes."
}
variable "platform_aws_cidrs" {
  type        = set(string)
  default     = ["10.80.0.0/16"]
  description = "Platform prefixes eligible for NCC import; this does not grant firewall access."
  validation {
    condition     = alltrue([for cidr in var.platform_aws_cidrs : can(cidrhost(cidr, 0))])
    error_message = "Platform prefixes must be valid CIDRs."
  }
}
variable "platform_database_endpoint_cidrs" {
  type        = set(string)
  default     = ["10.31.0.2/32"]
  description = "Platform-owned database PSC endpoints in the VPN VPC, advertised to AWS."
}
variable "router_asn" {
  type        = number
  default     = 65001
  description = "Private Cloud Router ASN."
  validation {
    condition     = (var.router_asn >= 64512 && var.router_asn <= 65534) || (var.router_asn >= 4200000000 && var.router_asn <= 4294967294)
    error_message = "Use a private BGP ASN."
  }
}
variable "aws_vpn_tunnels" {
  type = map(object({
    gcp_interface  = number
    aws_interface  = number
    outside_ip     = string
    inside_cidr    = string
    gcp_inside_ip  = string
    aws_inside_ip  = string
    aws_asn        = number
    secret_version = number
  }))
  default     = {}
  description = "Empty for gateway bootstrap, otherwise the complete four-tunnel AWS handoff."
}
variable "vpn_shared_secrets" {
  type        = map(string)
  default     = {}
  sensitive   = true
  ephemeral   = true
  description = "Ephemeral tunnel keys used only by the provider write-only field."
}
variable "inspection_mode" {
  description = "enabled inspects traffic; bypass keeps endpoints with ordinary allow rules; disabled removes endpoints while retaining routing and basic firewall rules."
  type        = string
  default     = "enabled"
  validation {
    condition     = contains(["enabled", "bypass", "disabled"], var.inspection_mode)
    error_message = "inspection_mode must be enabled, bypass or disabled."
  }
}

variable "compose_service_account" {
  type        = string
  default     = null
  description = "Optional consolidated Compose host service account allowed to reach the database proxy endpoint."
}
