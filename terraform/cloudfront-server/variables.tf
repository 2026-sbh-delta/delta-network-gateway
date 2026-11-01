variable "name" {
  description = "Name prefix for the CloudFront central ANF demo."
  type        = string
  default     = "delta"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,19}$", var.name))
    error_message = "name must be 2-20 lowercase letters, digits or hyphens, beginning with a letter."
  }
}
variable "environment" {
  description = "Environment name."
  type        = string
  default     = "lab"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,15}$", var.environment))
    error_message = "environment must be 2-16 lowercase letters, digits or hyphens."
  }
}
variable "region" {
  description = "Existing server Region; this test is verified in Seoul only."
  type        = string
  default     = "ap-northeast-2"
  validation {
    condition     = var.region == "ap-northeast-2"
    error_message = "This test root requires ap-northeast-2."
  }
}
variable "workload_account_id" {
  description = "Authorized owner of the EC2 origin and its VPC."
  type        = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.workload_account_id))
    error_message = "workload_account_id must contain 12 digits."
  }
}
variable "vpc_id" {
  description = "Existing private Server VPC, distinct from the internal ALB VPC."
  type        = string
  validation {
    condition     = can(regex("^vpc-[0-9a-f]{17}$", var.vpc_id))
    error_message = "vpc_id must be a full VPC ID."
  }
}
variable "server_instance_id" {
  description = "Existing running, private-only EC2 instance."
  type        = string
  validation {
    condition     = can(regex("^i-[0-9a-f]{17}$", var.server_instance_id))
    error_message = "server_instance_id must be a full EC2 instance ID."
  }
}
variable "server_security_group_id" {
  description = "Existing server SG; only new standalone ingress rules are owned here."
  type        = string
  validation {
    condition     = can(regex("^sg-[0-9a-f]{17}$", var.server_security_group_id))
    error_message = "server_security_group_id must be a full SG ID."
  }
}
variable "expected_server_private_ip" {
  description = "Expected existing server address, verified before creating CloudFront resources."
  type        = string
  validation {
    condition = try(
      cidrnetmask("${var.expected_server_private_ip}/32") != "" &&
      can(regex("^(10\\.|192\\.168\\.|172\\.(1[6-9]|2[0-9]|3[01])\\.)", var.expected_server_private_ip)), false
    )
    error_message = "expected_server_private_ip must be a private RFC1918 IPv4 address."
  }
}
variable "bootstrap_origin_access" {
  description = "Temporarily permit the CloudFront managed IPv4 prefix list on ALB TCP 80 for first Origin creation. Set false after the service SG rule exists."
  type        = bool
  default     = false
}
variable "tags" {
  description = "Additional common tags."
  type        = map(string)
  default     = {}
}
variable "ingress_vpc_id" {
  description = "Existing Client VPC where the internal ALB is placed, distinct from the Server VPC."
  type        = string
  validation {
    condition     = can(regex("^vpc-[0-9a-f]{17}$", var.ingress_vpc_id)) && var.ingress_vpc_id != var.vpc_id
    error_message = "ingress_vpc_id must be a full VPC ID distinct from the Server VPC."
  }
}
variable "alb_subnets" {
  description = "Two physical AZ IDs mapped to dedicated canonical private /24 ALB subnet CIDRs."
  type        = map(string)
  validation {
    condition = length(var.alb_subnets) == 2 && alltrue([for az, cidr in var.alb_subnets :
      can(regex("^apne2-az[0-9]+$", az)) && try(cidrnetmask(cidr) != "" && split("/", cidr)[1] == "24" && cidrhost(cidr, 0) == split("/", cidr)[0], false)
    ])
    error_message = "alb_subnets requires two Seoul physical AZ IDs and canonical IPv4 /24s."
  }
}
variable "transit_gateway_id" {
  description = "Existing central TGW used by the Client and Server attachments."
  type        = string
  validation {
    condition     = can(regex("^tgw-[0-9a-f]{17}$", var.transit_gateway_id))
    error_message = "transit_gateway_id must be a full TGW ID."
  }
}
variable "central_account_id" {
  description = "Authorized central TGW owner."
  type        = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.central_account_id)) && var.central_account_id != var.workload_account_id
    error_message = "central_account_id must be 12 digits and different from the Workload account."
  }
}
