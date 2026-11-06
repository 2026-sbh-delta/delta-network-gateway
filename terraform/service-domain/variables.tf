variable "project_id" {
  type    = string
  default = "term2-delta"
}
variable "region" {
  type    = string
  default = "asia-northeast3"
}
variable "zone" {
  description = "운영 앱 VM 영역."
  type        = string
  default     = "asia-northeast3-a"
}
variable "network_name" {
  description = "운영 앱 VM 네트워크(release-runtime-gcp 와 같다)."
  type        = string
  default     = "delta-lab-workload"
}
variable "app_instance" {
  description = "GCP 운영 앱 VM 이름(배포기가 대상별로 고정 이름 delta-app-<environment_id> 로 만든다)."
  type        = string
  default     = "delta-app-e9fb2216-03df-5e2f-b766-4cccf10395ba"
}
variable "aws_region" {
  type    = string
  default = "ap-northeast-2"
}

variable "cloudflare_zone_id" {
  description = "서비스 도메인 zone(32자리 16진수). 비밀값 아님."
  type        = string
  default     = "51d75b884cd0a1962c31e48f910ef9a3"
}
variable "service_domain" {
  description = "사용자가 쓰는 서비스 도메인. DNS 전용 CNAME 이고 대상은 파이프라인 도메인 전환 단계만 바꾼다(이 루트는 관리하지 않는다)."
  type        = string
  default     = "app.adajosou.site"
}
variable "green_domain" {
  description = "지금 서비스 중인 AWS 입구 이름(CNAME → AWS 운영 ALB)."
  type        = string
  default     = "aws.adajosou.site"
}
variable "blue_domain" {
  description = "옮겨 갈 GCP 입구 이름(A → GCP 운영 LB 주소)."
  type        = string
  default     = "gcp.adajosou.site"
}
variable "green_origin" {
  description = "AWS 운영 ALB DNS 이름(호원님 terraform/workload 앱 ALB)."
  type        = string
  default     = "delta-lab-notes-alb-1242820262.ap-northeast-2.elb.amazonaws.com"
}
variable "health_path" {
  description = "LB 상태 확인·전환 확인 경로. FE 를 거쳐 BE·DB 까지 확인한다."
  type        = string
  default     = "/api/health"
}
variable "green_health_path" {
  description = "AWS 입구 확인 경로(되돌리기 사전 확인). AWS 앱 ALB 대상 그룹 상태 확인과 같은 경로."
  type        = string
  default     = "/healthz"
}
