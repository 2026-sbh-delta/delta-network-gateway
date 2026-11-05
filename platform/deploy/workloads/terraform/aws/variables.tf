variable "account_id" { type = string }
variable "ami_id" { type = string }
variable "subnet_id" { type = string }
variable "security_group_ids" { type = list(string) }
variable "instance_profile" { type = string }
variable "environment_id" { type = string }
variable "service_id" { type = string }
variable "profile_id" {
  type = string
  validation {
    condition     = contains(["t3.micro", "t3.small"], var.profile_id)
    error_message = "지원 사양: t3.micro, t3.small"
  }
}
