variable "account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "AWS 계정 ID 12자리가 필요합니다."
  }
}
variable "region" {
  type    = string
  default = "ap-northeast-2"
}
variable "platform_instance_id" { type = string }
variable "tooling_instance_id" { type = string }
variable "platform_route_table_id" { type = string }
variable "tooling_route_table_id" { type = string }
variable "platform_security_group_id" { type = string }
variable "tooling_security_group_id" { type = string }
