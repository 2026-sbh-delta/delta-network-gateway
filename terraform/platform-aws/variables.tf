variable "name" {
  description = "리소스 이름 접두사. 기존 delta-platform-* 자원과 겹치지 않게 한다."
  type        = string
  default     = "delta-cp"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}$", var.name))
    error_message = "name은 소문자로 시작하는 2~21자 소문자·숫자·하이픈이어야 한다."
  }
}

variable "account_id" {
  description = "Provider가 허용할 AWS 계정 ID(12자리)."
  type        = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "account_id는 12자리 숫자여야 한다."
  }
}

variable "region" {
  description = "플랫폼 리전."
  type        = string
  default     = "ap-northeast-2"
}

variable "vpc_cidr" {
  description = "플랫폼 VPC 대역. 기존 10.10·10.20·10.80·172.31 대역과 겹치면 안 된다."
  type        = string
  default     = "10.90.0.0/16"
  validation {
    condition     = can(cidrnetmask(var.vpc_cidr)) && tonumber(split("/", var.vpc_cidr)[1]) <= 16
    error_message = "vpc_cidr는 /16 이상 크기의 IPv4 대역이어야 한다."
  }
}

variable "availability_zones" {
  description = "ALB가 요구하는 두 AZ. 인스턴스와 NAT는 첫 번째 AZ에 둔다."
  type        = list(string)
  default     = ["ap-northeast-2a", "ap-northeast-2c"]
  validation {
    condition     = length(var.availability_zones) == 2
    error_message = "availability_zones는 정확히 두 개여야 한다."
  }
}

variable "sast_instance_type" {
  description = "SAST 인스턴스 타입(Graviton)."
  type        = string
  default     = "t4g.medium"
}

variable "dast_instance_type" {
  description = "DAST 인스턴스 타입(Graviton). Chrome과 에이전트를 함께 돌린다."
  type        = string
  default     = "t4g.large"
}

variable "small_instance_type" {
  description = "오케스트레이터·차분 에이전트·로그 인스턴스 타입(Graviton)."
  type        = string
  default     = "t4g.small"
}

variable "jenkins_instance_type" {
  description = "Jenkins 컨트롤러·빌드 에이전트 인스턴스 타입(x86)."
  type        = string
  default     = "t3.medium"
}

variable "spot_services" {
  # 오케스트레이터(t4g.small)는 11/5 서울 2a Spot 용량 부족(InsufficientInstanceCapacity)으로 온디맨드로 둔다.
  # 실행 상태·승인 대기를 들고 있어 회수되면 안 되는 호스트이기도 하다.
  description = "Spot으로 띄울 서비스. 온디맨드 vCPU 한도 증설(2026-11-05 요청)이 승인되면 빈 목록으로 바꾼다."
  type        = list(string)
  default     = ["jenkins", "jenkins-agent"]
}

variable "target_aws_account_id" {
  description = "A 기업(이관 원본) AWS 계정. 지금은 같은 계정을 쓴다."
  type        = string
  default     = "616723161368"
}

variable "tags" {
  description = "공통 태그 추가/재정의."
  type        = map(string)
  default     = {}
}

variable "bundle_ecr_repository" {
  description = "빌드 묶음을 복사할 A 기업 ECR 저장소 이름(AWS 스테이징·운영 공용)."
  type        = string
  default     = "delta-lab-staging"
}

variable "release_state_bucket" {
  description = "운영 배포(환경별 VM·DB) Terraform state 버킷. 키 접두사 delta-cp/release/."
  type        = string
  default     = "sbh-delta-workload-tf-616723161368"
}
