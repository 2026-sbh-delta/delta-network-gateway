variable "name" {
  description = "Resource name prefix."
  type        = string
  default     = "delta"
}

variable "environment" {
  description = "Environment name and tag."
  type        = string
  default     = "lab"
}

variable "region" {
  description = "AWS Region."
  type        = string
  default     = "ap-northeast-2"
}

variable "account_id" {
  description = "AWS account the provider is allowed to touch."
  type        = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "account_id must be a 12-digit AWS account ID."
  }
}

variable "vpc_id" {
  description = "Existing VPC for the tooling host."
  type        = string
}

variable "subnet_id" {
  description = "Public subnet (routed to an internet gateway). The host keeps a fixed Elastic IP for egress."
  type        = string
}

variable "instance_type" {
  description = "Graviton (arm64) instance type. Images are built for linux/arm64."
  type        = string
  default     = "t4g.large"
  validation {
    condition     = can(regex("^[a-z0-9]+g[a-z0-9]*\\.", var.instance_type))
    error_message = "instance_type must be a Graviton (arm64) type such as t4g.large."
  }
}

variable "root_volume_gb" {
  description = "Root EBS volume size in GiB (images, scan state, DAST evidence)."
  type        = number
  default     = 40
}

variable "parameter_path" {
  description = "SSM Parameter Store path the host may read (for example an LLM credential stored as SecureString)."
  type        = string
  default     = "/delta/tooling/"
  validation {
    condition     = can(regex("^/[A-Za-z0-9_./-]+/$", var.parameter_path))
    error_message = "parameter_path must start and end with a slash."
  }
}

variable "enable_outbound_federation" {
  description = "Allow the host role to call sts:GetWebIdentityToken (AWS outbound identity federation for short-lived LLM credentials)."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Extra tags."
  type        = map(string)
  default     = {}
}
