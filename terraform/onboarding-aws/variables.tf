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

variable "prod_hosts" {
  description = "운영 VM(Docker Compose) 배포 대상. Jenkins가 EC2 Instance Connect Endpoint로 SSH 터널을 연다. 호원님 workload 인스턴스."
  type = map(object({
    instance_id = string
    endpoint_id = string
    private_ip  = string
    os_user     = string
  }))
  default = {
    a-corp-aws = {
      instance_id = "i-0637ad326681c7480"
      endpoint_id = "eice-03436798a3c0585a5"
      private_ip  = "10.10.1.211"
      os_user     = "ec2-user"
    }
  }
}

variable "tfstate_bucket" {
  description = "A 기업 Terraform state 버킷."
  type        = string
}

variable "app_tfstate_prefixes" {
  description = "이관 대상 앱 인프라(EC2·RDS·ALB, terraform/workload 앱 모드) state 키 접두사."
  type        = list(string)
  default     = ["delta/lab/workload-client/"]
}

variable "central_tfstate_bucket" {
  description = "중앙 네트워크·보안 Terraform state 버킷(호원님 관리). 버킷 정책이 이 계정을 허용한다."
  type        = string
  default     = "sbh-delta-central-tf"
}

variable "tfstate_prefixes" {
  description = "차분 에이전트가 읽을 A 기업 state 키 접두사(플랫폼 state는 넣지 않는다)."
  type        = list(string)
  default     = ["delta/lab/staging-aws/"]
}
