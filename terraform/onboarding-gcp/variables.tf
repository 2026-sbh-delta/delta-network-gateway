variable "project_id" {
  description = "A 기업 GCP 프로젝트(이관 대상)."
  type        = string
  default     = "term2-delta"
}

variable "region" {
  type    = string
  default = "asia-northeast3"
}

variable "platform_aws_account_id" {
  description = "플랫폼(제어 영역)이 있는 AWS 계정. 이 계정의 역할만 WIF로 들어올 수 있다."
  type        = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.platform_aws_account_id))
    error_message = "12자리 AWS 계정 ID여야 한다."
  }
}

variable "platform_role_prefix" {
  description = "플랫폼 역할 이름 접두사. 이 접두사가 아닌 역할은 풀에 들어올 수 없다."
  type        = string
  default     = "delta-cp-"
}

variable "staging_name" {
  description = "terraform/staging-gcp가 만든 자원 이름 접두사(name-environment)."
  type        = string
  default     = "delta-lab"
}

variable "tfstate_bucket" {
  description = "A 기업 Terraform state 버킷. 차분 에이전트는 읽기만, 인프라 적용은 쓰기."
  type        = string
  default     = "term2-delta-tfstate"
}

variable "release_deployer_account" {
  description = "운영 배포(환경별 VM 생성)에 쓰는 기존 배포 계정 이메일."
  type        = string
  default     = "delta-platform-deployer@term2-delta.iam.gserviceaccount.com"
}

variable "release_app_account" {
  description = "운영 앱 VM 서비스 계정 이메일(기존 runtime-gcp 계정)."
  type        = string
  default     = "delta-platform-app@term2-delta.iam.gserviceaccount.com"
}

variable "release_network" {
  description = "운영 앱 VM 네트워크(A 기업 GCP 워크로드 VPC)."
  type        = string
  default     = "delta-lab-workload"
}

variable "release_app_subnet_cidr" {
  description = "운영 앱 VM·PostgreSQL 사설 연결 주소가 있는 서브넷 대역(delta-lab-app-backend)."
  type        = string
  default     = "10.32.2.0/24"
}
