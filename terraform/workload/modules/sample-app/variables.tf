variable "prefix" {
  description = "Application resource prefix."
  type        = string
}

variable "region" {
  description = "AWS region."
  type        = string
}

variable "account_id" {
  description = "Expected workload AWS account ID."
  type        = string
}

variable "vpc_id" {
  description = "Existing workload VPC ID."
  type        = string
}

variable "vpc_cidr" {
  description = "Existing workload VPC CIDR."
  type        = string
}

variable "existing_subnet_cidrs" {
  description = "Every existing subnet CIDR for overlap validation."
  type        = list(string)
}

variable "management_security_group_id" {
  description = "Existing EIC endpoint security group."
  type        = string
}

variable "management_endpoint_id" {
  description = "Existing EIC endpoint ID for deployment inventory."
  type        = string
}

variable "instance_az_id" {
  description = "Existing AZ for both EC2 instances, RDS primary and NAT."
  type        = string
}

variable "workload_subnets" {
  description = "Existing private workload subnet and route table per physical AZ ID."
  type        = map(object({ id = string, route_table_id = string, az_name = string }))
}

variable "config" {
  description = "Pinned x86 application configuration."
  type = object({
    public_subnet_cidrs  = optional(map(string), { apne2-az1 = "10.10.10.0/24", apne2-az2 = "10.10.11.0/24" })
    db_subnet_cidrs      = optional(map(string), { apne2-az1 = "10.10.20.0/24", apne2-az2 = "10.10.21.0/24" })
    ami_id               = optional(string)
    instance_type        = optional(string, "t3.small")
    db_engine_version    = optional(string, "18.3")
    db_instance_class    = optional(string, "db.t3.micro")
    db_allocated_storage = optional(number, 20)
    certificate_arn      = optional(string)
    hostname             = optional(string)
    node_version         = optional(string, "24.19.0")
    node_sha256          = optional(string, "14b342e71204f811bde6153be8e04b62aef63c236fef92b55f9c83154b409647")
  })
  default = {}
  validation {
    condition     = can(regex("^24[.][0-9]+[.][0-9]+$", var.config.node_version)) && can(regex("^[a-f0-9]{64}$", var.config.node_sha256)) && var.config.db_allocated_storage >= 20 && floor(var.config.db_allocated_storage) == var.config.db_allocated_storage && can(regex("^[0-9]+[.][0-9]+$", var.config.db_engine_version))
    error_message = "Use a pinned Node.js 24 version and SHA-256, integer DB storage >=20 GiB and a major.minor PostgreSQL version."
  }
  validation {
    condition     = (var.config.certificate_arn == null || try(can(regex("^[a-zA-Z0-9.-]+[.][a-zA-Z]{2,}$", var.config.hostname)), false)) && can(regex("^db[.](t3|t3a|m[0-9]+[id]*|r[0-9]+[id]*)[.]", var.config.db_instance_class))
    error_message = "HTTPS requires the certificate's hostname; the RDS class must use an x86 family."
  }

}
