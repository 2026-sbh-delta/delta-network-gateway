variable "account_id" {

  type        = string
  description = "실제 STS로 확인한 배포 계정"
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "12자리 AWS 계정 ID가 필요합니다."
  }

}
variable "region" {
  type        = string
  default     = "ap-northeast-2"
  description = "배포 리전"
}
variable "custom_domain" {
  type        = string
  default     = null
  description = "관리 플랫폼의 선택적 HTTPS 호스트명. 인증서 ARN과 함께 지정"
  validation {
    condition     = var.custom_domain == null ? true : can(regex("^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", var.custom_domain))
    error_message = "와일드카드·URL·대문자가 없는 DNS 호스트명을 사용하세요."
  }
}
variable "custom_domain_certificate_arn" {
  type        = string
  default     = null
  description = "custom_domain을 포함하는 발급 완료된 동일 계정 us-east-1 ACM 인증서 ARN"
  validation {
    condition     = var.custom_domain_certificate_arn == null ? true : can(regex("^arn:aws:acm:us-east-1:[0-9]{12}:certificate/[0-9a-f-]+$", var.custom_domain_certificate_arn))
    error_message = "CloudFront에는 us-east-1 ACM 인증서 ARN이 필요합니다."
  }
}
variable "availability_zone" {
  type        = string
  description = "앱·데이터 디스크·NAT를 배치할 AZ"
}
variable "ami_id" {
  type        = string
  description = "확인한 AL2023 ARM64 AMI ID"
}
variable "name" {

  type        = string
  default     = "delta-platform"
  description = "리소스 이름과 SSM 경로 접두어"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,30}$", var.name))
    error_message = "영문 소문자·숫자·하이픈의 3~31자 이름을 사용하세요."
  }

}
variable "bootstrap_origin_access" {

  type        = bool
  default     = false
  description = "최초 VPC Origin 생성 시만 true, 서비스 SG 생성 후 false로 제거"

}
variable "instance_type" {

  type        = string
  default     = "t4g.small"
  description = "플랫폼 실행 VM, ARM64 소형 인스턴스"
  validation {
    condition     = contains(["t4g.small", "t4g.medium"], var.instance_type)
    error_message = "t4g.small 또는 t4g.medium을 사용하세요."
  }

}
variable "data_volume_size" {
  type        = number
  default     = 20
  description = "DB 영속 gp3 디스크 용량 GiB"
}
variable "jenkins_ami_id" {
  type        = string
  default     = "ami-05becc39a92b78fa5"
  description = "서울 리전의 확인한 AL2023 x86_64 AMI, Jenkins 전용 VM"
  validation {
    condition     = can(regex("^ami-[a-f0-9]{17}$", var.jenkins_ami_id))
    error_message = "확인한 x86_64 AMI ID를 사용하세요."
  }
}
