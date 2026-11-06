variable "region" {
  type    = string
  default = "ap-northeast-2"
}
variable "account_id" { type = string }
variable "assume_role_arn" {
  type = string
  validation {
    condition     = can(regex("^arn:aws:iam::[0-9]{12}:role/[A-Za-z0-9+=,.@_/-]+$", var.assume_role_arn))
    error_message = "대상 계정 배포 역할 ARN이 필요합니다."
  }
}
variable "external_id" { type = string }
variable "ami_id" { type = string }
variable "subnet_id" { type = string }
variable "security_group_ids" { type = list(string) }
variable "instance_profile" { type = string }
variable "environment_id" { type = string }
variable "service_id" { type = string }
variable "profile_id" {
  type = string
  validation {
    condition     = contains(["t3.small"], var.profile_id)
    error_message = "지원 사양: t3.small"
  }
}
