variable "project_id" {
  type    = string
  default = "term2-delta"
}
variable "region" {
  type    = string
  default = "asia-northeast3"
}
variable "zone" {
  description = "운영 앱 VM 영역(배포기 VM 관리 권한의 조건 범위)."
  type        = string
  default     = "asia-northeast3-a"
}
variable "network_name" {
  description = "운영 앱 VM 네트워크(A 기업 GCP 워크로드 VPC)."
  type        = string
  default     = "delta-lab-workload"
}
variable "subnet_name" {
  description = "운영 앱 VM 서브넷. NAT·배포기 서브넷 사용 권한 대상."
  type        = string
  default     = "delta-lab-app-backend"
}
variable "network_tag" {
  description = "인터넷(NAT) 경로를 받는 운영 앱 VM 태그. 배포 설정의 network_tag 와 같아야 한다."
  type        = string
  default     = "delta-platform-customer-app"
}
variable "legacy_database_address" {
  description = "기존 동규님 시연 DB 사설 주소(송신 5432 허용). 우리 PG18 은 onboarding-gcp 의 앱 서브넷 규칙을 쓴다."
  type        = string
  default     = "10.32.3.2"
}
variable "legacy_deployer_cidr" {
  description = "기존 AWS 배포 VM(동규님 플랫폼)의 헬스 확인 출발지. 우리 배포기는 IAP 로 VM 안에서 확인해 쓰지 않는다."
  type        = string
  default     = "10.80.11.108/32"
}
variable "proxy_cidr" {
  description = "운영 LB 프록시 전용 서브넷 대역."
  type        = string
  default     = "10.32.4.0/23"
}

variable "app_subnet_cidr" {
  description = "운영 앱 VM·PostgreSQL 사설 연결 주소가 있는 서브넷 대역(delta-lab-app-backend)."
  type        = string
  default     = "10.32.2.0/24"
}
variable "build_repository" {
  description = "빌드 묶음 이미지 저장소(스테이징 AR) ID."
  type        = string
  default     = "delta-lab-staging"
}
