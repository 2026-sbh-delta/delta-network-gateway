variable "target_account_id" {
  description = "A 기업 AWS 계정(이관 원본). 이 계정에 플랫폼이 위임받을 역할을 만든다."
  type        = string
}

variable "platform_account_id" {
  description = "플랫폼(제어 영역) AWS 계정. 지금은 같은 계정을 쓴다."
  type        = string
}

variable "platform_role_prefix" {
  type    = string
  default = "delta-cp-"
}

variable "external_id" {
  description = "혼동된 대리인 방지용 ExternalId. 플랫폼이 위임받을 때 같은 값을 보낸다(비밀값 아님)."
  type        = string
  default     = "delta-cp-a-corp"
}

variable "region" {
  type    = string
  default = "ap-northeast-2"
}

variable "staging_prefix" {
  description = "terraform/staging-aws 자원 이름 접두사."
  type        = string
  default     = "delta-lab-staging"
}

variable "prod_prefix" {
  description = "운영 ECS 자원 이름 접두사."
  type        = string
  default     = "delta-lab-prod"
}

variable "tfstate_bucket" {
  description = "A 기업 Terraform state 버킷."
  type        = string
}

variable "tfstate_prefixes" {
  description = "차분 에이전트가 읽을 A 기업 state 키 접두사(플랫폼 state는 넣지 않는다)."
  type        = list(string)
  default     = ["delta/lab/staging-aws/"]
}
