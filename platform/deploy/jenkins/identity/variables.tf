variable "account_id" {
  type        = string
  description = "빌드 에이전트 IAM 역할을 만들 AWS 계정"
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "12자리 계정 ID가 필요합니다."
  }
}
variable "region" {
  type        = string
  default     = "ap-northeast-2"
  description = "ECR 리전"
}
variable "repository_name" {
  type        = string
  default     = "delta-platform-builds"
  description = "이미 있는 고객 이미지 ECR 저장소. 새 저장소를 만들지 않음"
}
variable "verification_principal_arn" {
  type        = string
  default     = null
  nullable    = true
  description = "로컬 실제 검증 중에만 AssumeRole을 허용할 같은 계정 IAM 역할. 완료 후 null로 제거"
  validation {
    condition     = var.verification_principal_arn == null ? true : startswith(var.verification_principal_arn, "arn:aws:iam::${var.account_id}:role/")
    error_message = "같은 계정의 IAM 역할만 사용할 수 있습니다."
  }
}
