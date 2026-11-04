variable "aws_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "AWS 수신 계정 ID 12자리가 필요합니다."
  }
}
variable "aws_region" {
  type    = string
  default = "ap-northeast-2"
}
variable "source_aws_accounts" {
  type = set(string)
  validation {
    condition     = length(var.source_aws_accounts) > 0 && alltrue([for a in var.source_aws_accounts : can(regex("^[0-9]{12}$", a))])
    error_message = "로그를 보낼 AWS 계정을 명시합니다. 와일드카드는 허용하지 않습니다."
  }
}
variable "gcp_project_id" {
  type    = string
  default = "term2-delta"
  validation {
    condition     = var.gcp_project_id == "term2-delta"
    error_message = "현재 수집기는 term2-delta 프로젝트로 범위를 제한합니다."
  }
}
variable "name" {
  type    = string
  default = "delta-platform-logs"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{3,23}$", var.name))
    error_message = "이름은 영문 소문자로 시작하는 4~24자입니다."
  }
}
variable "retention_days" {
  type    = number
  default = 3
  validation {
    condition     = contains([1, 3, 5, 7, 14, 30], var.retention_days)
    error_message = "해커톤 로그 보관 기간은 1, 3, 5, 7, 14, 30일 중 선택합니다."
  }
}
variable "aws_lambda_zip" {
  type        = string
  description = "build.py로 만든 AWS 수집기 ZIP의 절대 경로"
}
variable "gcp_lambda_zip" {
  type        = string
  description = "services/gcp-log-collector/build.py로 만든 Linux Lambda ZIP의 절대 경로"
}
variable "gcp_log_filter" {
  type        = string
  default     = null
  description = "null이면 VM·Cloud Run·네트워크 로그와 검증 로그를 수집합니다. 기존 원본 로그 보관에는 영향이 없습니다."
  validation {
    condition     = var.gcp_log_filter == null ? true : length(trimspace(var.gcp_log_filter)) > 0
    error_message = "빈 필터로 프로젝트의 모든 로그를 수집하지 않습니다."
  }
}
