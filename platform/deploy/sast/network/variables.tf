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

variable "dast_sandbox" {
  description = "기존 플랫폼 내부 ALB와 각 ALB 서브넷의 Route Table. null이면 기존 SAST 연결만 유지."
  type = object({
    alb_arn             = string
    subnet_route_tables = map(string)
  })
  default = null
  validation {
    condition     = var.dast_sandbox == null ? true : length(var.dast_sandbox.subnet_route_tables) == 2
    error_message = "기존 내부 ALB의 두 서브넷과 각각의 Route Table을 지정하세요."
  }
}
