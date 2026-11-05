variable "account_id" { type = string }
variable "region" {
  type    = string
  default = "ap-northeast-2"
}
variable "name" {
  type = string
  validation {
    condition     = can(regex("^delta-platform-[a-z0-9-]{1,40}$", var.name))
    error_message = "Use a platform-owned database name."
  }
}
variable "subnet_ids" { type = list(string) }
variable "security_group_ids" { type = list(string) }
variable "service_id" { type = string }
variable "environment_id" { type = string }
