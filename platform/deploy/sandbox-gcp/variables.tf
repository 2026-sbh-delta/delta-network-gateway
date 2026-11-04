variable "project_id" {
  type = string
}
variable "region" {
  type    = string
  default = "asia-northeast3"
}
variable "subnet_cidr" {
  type    = string
  default = "10.33.0.0/24"
}
variable "aws_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "AWS account ID must contain 12 digits."
  }
}
variable "platform_role_name" {
  type    = string
  default = "delta-platform-app"
}
variable "scanner_role_name" {
  type    = string
  default = "delta-lab-tooling-host"
}
