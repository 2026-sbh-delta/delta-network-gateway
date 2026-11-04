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
variable "aws_cidrs" {
  type        = set(string)
  description = "Approved AWS workload prefixes; NCC imports only these prefixes."
}
variable "internet_source_cidrs" {
  type        = set(string)
  description = "Explicit IPv4 sources allowed to exercise the public demo."
  validation {
    condition     = length(var.internet_source_cidrs) > 0 && !contains(var.internet_source_cidrs, "0.0.0.0/0")
    error_message = "Specify limited demo source CIDRs, not an unrestricted internet rule."
  }
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
variable "server_image" {
  type        = string
  description = "Pinned Ubuntu 24.04 image self-link, never an image family."
  validation {
    condition     = can(regex("(^|/)projects/ubuntu-os-cloud/global/images/ubuntu-2404-", var.server_image)) && !strcontains(var.server_image, "/family/")
    error_message = "Supply a pinned Ubuntu 24.04 image path."
  }
}
variable "aws_collector_role_arn" {
  type        = string
  description = "Exact central AWS collector role trusted by Google WIF."
  validation {
    condition     = can(regex("^arn:aws:iam::[0-9]{12}:role/[A-Za-z0-9+=,.@_/-]+$", var.aws_collector_role_arn))
    error_message = "Supply an exact IAM role ARN, not a wildcard principal."
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
variable "deletion_protection" {
  type        = bool
  default     = true
  description = "Protect the demo VM from accidental deletion."
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
