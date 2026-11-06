variable "account_id" { type = string }
variable "region" {
  type    = string
  default = "ap-northeast-2"
}
variable "assume_role_arn" {
  type = string
  validation {
    condition     = can(regex("^arn:aws:iam::[0-9]{12}:role/[A-Za-z0-9+=,.@_/-]+$", var.assume_role_arn))
    error_message = "대상 계정 배포 역할 ARN이 필요합니다."
  }
}
variable "external_id" { type = string }
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
