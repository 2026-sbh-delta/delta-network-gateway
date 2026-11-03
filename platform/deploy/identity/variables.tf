variable "account_id" {
  type        = string
  description = "배포 계정"
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "12자리 AWS 계정 ID를 사용하세요."
  }
}
variable "region" {
  type        = string
  default     = "ap-northeast-2"
  description = "플랫폼 리전"
}
variable "name" {
  type        = string
  default     = "delta-platform"
  description = "기존 플랫폼 리소스·역할 이름 접두어"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,30}$", var.name))
    error_message = "안전한 플랫폼 이름이 필요합니다."
  }
}
variable "github_subject_prefix" {
  type        = string
  description = "GitHub API에서 확인한 immutable sub 접두어"
  validation {
    condition     = can(regex("^repo:[A-Za-z0-9-]+@[0-9]+/[A-Za-z0-9_.-]+@[0-9]+$", var.github_subject_prefix))
    error_message = "조직·저장소 ID가 포함된 정확한 접두어를 사용하세요. wildcard는 허용하지 않습니다."
  }
}
variable "create_oidc_provider" {
  type        = bool
  default     = false
  description = "계정에 GitHub OIDC provider가 없을 때만 true"
}
variable "state_bucket" {
  type        = string
  description = "기존 플랫폼 Terraform state 버킷"
}
variable "origin_request_policy_id" {
  type        = string
  description = "기존 플랫폼 CloudFront Origin Request Policy ID"
}
variable "cloudfront_prefix_list_id" {
  type        = string
  description = "기존 플랫폼이 사용하는 AWS 관리 CloudFront prefix list ID"
}
variable "platform_vpc_id" {
  type        = string
  description = "플랫폼 VPC ID, VM 생성·연결 범위"
}
variable "platform_subnet_ids" {
  type        = set(string)
  description = "기존 플랫폼 public/private subnet ID"
}
