variable "account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "12자리 AWS 계정 ID가 필요합니다."
  }
}
variable "region" {
  type    = string
  default = "ap-northeast-2"
}
variable "subnet_ids" {
  type = object({ sast = string, dast = string })
}
variable "platform_security_group_id" { type = string }
variable "ami_id" { type = string }
variable "tooling_instance_profile" {
  type        = string
  default     = "delta-lab-tooling-host"
  description = "기존 Anthropic/GCP WIF를 유지하는 도구 실행 역할. 이 모듈은 해당 역할을 변경하지 않는다."
}
