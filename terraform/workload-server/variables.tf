variable "name" {
  description = "Resource name prefix."
  type        = string
  default     = "delta"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,19}$", var.name))
    error_message = "name must be a lowercase resource prefix of at most 20 characters."
  }
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
  description = "Exactly two physical AZ IDs and dedicated private /24 TGW/server subnets."
  type = map(object({
    tgw_subnet_cidr    = string
    server_subnet_cidr = string
  }))
  validation {
    condition = length(var.azs) == 2 && alltrue(flatten([for az_id, az in var.azs : [
      can(regex("^[a-z0-9]+-az[0-9]+$", az_id)),
      contains(var.central_az_ids, az_id),
      alltrue([for cidr in [az.tgw_subnet_cidr, az.server_subnet_cidr] : try(
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
  description = "Central Inspection VPC CIDRs to check for address overlap, not server routes."
  type        = set(string)
  validation {
    condition = length(var.inspection_cidrs) > 0 && alltrue([for cidr in var.inspection_cidrs : try(
      cidrnetmask(cidr) != "" && cidrhost(cidr, 0) == split("/", cidr)[0], false
    )])
    error_message = "inspection_cidrs must contain canonical IPv4 networks."
  }
}

variable "server_az_id" {
  description = "Selected AZ for the single EC2 server and management endpoint."
  type        = string
  validation {
    condition     = contains(keys(var.azs), var.server_az_id)
    error_message = "server_az_id must be a key in azs."
  }
}

variable "ami_id" {
  description = "Pinned Amazon AL2023 standard ARM64 AMI ID, verified by an AWS data source."
  type        = string
  validation {
    condition     = can(regex("^ami-[0-9a-f]{17}$", var.ami_id))
    error_message = "ami_id must be a full AMI ID."
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
  description = "Registered private remote networks routed to TGW; routed only after central acceptance when enable_remote_routes=true."
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

variable "tags" {
  description = "Additional common tags; Name remains resource specific."
  type        = map(string)
  default     = {}
}

variable "server_private_ip" {
  description = "Fixed IPv4 in the selected server /24, excluding AWS reserved addresses."
  type        = string
  validation {
    condition     = try(cidrnetmask("${var.server_private_ip}/32") != "" && cidrhost("${var.server_private_ip}/24", 0) == cidrhost(var.azs[var.server_az_id].server_subnet_cidr, 0) && tonumber(split(".", var.server_private_ip)[3]) >= 4 && tonumber(split(".", var.server_private_ip)[3]) <= 254, false)
    error_message = "server_private_ip must be a usable IPv4 in the selected server subnet."
  }
}
variable "allowed_client_cidrs" {
  description = "Private client /32s allowed to both TCP control listeners; must be within remote_cidrs."
  type        = set(string)
  default     = []
  validation {
    condition     = alltrue([for cidr in var.allowed_client_cidrs : try(cidrnetmask(cidr) != "" && split("/", cidr)[1] == "32" && cidrhost(cidr, 0) == split("/", cidr)[0], false)])
    error_message = "allowed_client_cidrs must contain canonical IPv4 /32s."
  }
}
variable "enable_remote_routes" {
  description = "Enable server-to-client TGW routes only after the central account accepts the attachment."
  type        = bool
  default     = false
  validation {
    condition     = !var.enable_remote_routes || (var.transit_gateway_id != null && length(var.remote_cidrs) > 0)
    error_message = "Remote routes require a TGW attachment and registered remote CIDRs."
  }
}
