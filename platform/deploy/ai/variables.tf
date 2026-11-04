variable "account_id" {
  type        = string
  description = "플랫폼 AWS 계정. 팀원 AI state의 계정/버킷과 수명을 공유하지 않는다."
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "12자리 AWS 계정 ID가 필요합니다."
  }
}
variable "region" {
  type    = string
  default = "ap-northeast-2"
}
variable "subnet_id" {
  type        = string
  description = "플랫폼 root가 만든 AI 전용 10.80.20.0/24 서브넷"
}
variable "platform_security_group_id" {
  type        = string
  description = "AI API 호출을 허용할 플랫폼 API/Worker SG"
}
variable "ami_id" {
  type        = string
  description = "서울 AL2023 ARM64 AMI. 변경은 별도 계획으로 검토한다."
}
variable "instance_type" {
  type    = string
  default = "t4g.small"
  validation {
    condition     = contains(["t4g.small", "t4g.medium"], var.instance_type)
    error_message = "해커톤에서는 t4g.small/medium만 사용합니다."
  }
}
