variable "project_id" { type = string }
variable "aws_account_id" { type = string }
variable "aws_deployer_role" { type = string }
variable "network_name" { type = string }
variable "subnet_name" { type = string }
variable "runtime_image" { type = string }
variable "database_address" { type = string }
variable "database_secret" { type = string }
variable "database_service_attachment" { type = string }
variable "vpn_network" { type = string }
variable "vpn_subnet" { type = string }
variable "worker_database_address" { type = string }
variable "deployer_cidr" { type = string }
variable "proxy_cidr" { type = string }
variable "zone" {
  type    = string
  default = "asia-northeast3-a"
}
