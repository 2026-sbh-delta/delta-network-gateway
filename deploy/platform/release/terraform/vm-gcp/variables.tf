variable "project_id" { type = string }
variable "region" {
  type    = string
  default = "asia-northeast3"
}
variable "zone" {
  type = string
  validation {
    condition     = contains(["asia-northeast3-a", "asia-northeast3-b", "asia-northeast3-c"], var.zone)
    error_message = "서울 리전 zone만 지원합니다."
  }
}
variable "subnetwork" { type = string }
variable "service_account" { type = string }
variable "image" { type = string }
variable "network_tag" { type = string }
variable "preinstalled_runtime" {
  type    = bool
  default = false
}
variable "environment_id" { type = string }
variable "service_id" { type = string }
variable "profile_id" {
  type = string
  validation {
    condition     = contains(["e2-small"], var.profile_id)
    error_message = "지원 사양: e2-small"
  }
}
