variable "project_id" { type = string }
variable "name" {
  type = string
  validation {
    condition     = can(regex("^delta-platform-[a-z0-9-]{1,40}$", var.name))
    error_message = "Use a platform-owned database name."
  }
}
variable "region" {
  type    = string
  default = "asia-northeast3"
}
variable "network" { type = string }
variable "subnetwork" { type = string }
variable "purpose" {
  type    = string
  default = "customer-app"
}
variable "admin_password" {
  type      = string
  sensitive = true
  ephemeral = true
}
variable "ca_pem" { type = string }
