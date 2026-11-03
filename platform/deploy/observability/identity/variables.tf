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
variable "platform_role_name" {
  type    = string
  default = "delta-platform-app"
  validation {
    condition     = startswith(var.platform_role_name, "delta-platform-")
    error_message = "플랫폼 전용 역할만 연결합니다."
  }
}
