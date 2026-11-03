variable "account_id" {
  type        = string
  description = "빌드 전용 기반을 생성할 AWS 계정"
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "12자리 계정 ID가 필요합니다."
  }
}
variable "region" {
  type        = string
  default     = "ap-northeast-2"
  description = "빌드·이미지 저장 리전"
}
variable "name" {
  type        = string
  default     = "delta-platform-builds"
  description = "CodeBuild·ECR·빌드 역할 이름"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,45}$", var.name))
    error_message = "소문자·숫자·하이픈으로 된 이름이 필요합니다."
  }
}
variable "platform_role_name" {
  type        = string
  default     = "delta-platform-app"
  description = "빌드 시작·조회 권한을 추가할 기존 플랫폼 VM 역할"
}
