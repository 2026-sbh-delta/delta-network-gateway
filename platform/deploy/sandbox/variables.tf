variable "account_id" {
  type        = string
  description = "샌드박스 생성 계정"
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "12자리 계정 ID가 필요합니다."
  }
}
variable "region" {
  type        = string
  default     = "ap-northeast-2"
  description = "플랫폼과 같은 AWS 리전"
}
variable "platform_private_subnet_id" {
  type        = string
  description = "플랫폼의 기존 10.80.1.0/24 사설 서브넷, ALB 한쪽에 사용"
}
variable "nat_instance_id" {
  type        = string
  description = "기존 플랫폼 NAT EC2, 새 태스크 서브넷의 HTTPS egress에 사용"
}
variable "second_az" {
  type        = string
  default     = "ap-northeast-2b"
  description = "ALB 두 번째 서브넷의 다른 AZ"
}
variable "bootstrap_image_digest" {
  type        = string
  description = "기존 delta-platform-builds 저장소의 성공한 linux/amd64 이미지 digest"
  validation {
    condition     = can(regex("^sha256:[a-f0-9]{64}$", var.bootstrap_image_digest))
    error_message = "sha256 digest만 허용합니다."
  }
}

locals { name = "delta-platform-sandbox" }
