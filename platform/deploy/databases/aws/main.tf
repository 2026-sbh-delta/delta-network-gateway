terraform {
  required_version = ">= 1.11, < 2.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}
provider "aws" {
  region              = var.region
  allowed_account_ids = [var.account_id]
}
locals {
  tags = {
    ManagedBy   = "delta-platform"
    ServiceId   = var.service_id
    Environment = var.environment_id
  }
}
resource "aws_db_subnet_group" "app" {
  name       = var.name
  subnet_ids = var.subnet_ids
  tags       = local.tags
}
resource "aws_db_parameter_group" "app" {
  name   = var.name
  family = "postgres17"
  parameter {
    name         = "rds.logical_replication"
    value        = "1"
    apply_method = "pending-reboot"
  }
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
  parameter {
    name  = "max_slot_wal_keep_size"
    value = "1024"
  }
  tags = local.tags
}
resource "aws_db_instance" "app" {
  identifier                  = var.name
  engine                      = "postgres"
  engine_version              = "17"
  instance_class              = "db.t4g.micro"
  allocated_storage           = 20
  max_allocated_storage       = 30
  storage_type                = "gp3"
  storage_encrypted           = true
  username                    = "platform_admin"
  manage_master_user_password = true
  db_subnet_group_name        = aws_db_subnet_group.app.name
  parameter_group_name        = aws_db_parameter_group.app.name
  vpc_security_group_ids      = var.security_group_ids
  publicly_accessible         = false
  multi_az                    = false
  backup_retention_period     = 1
  copy_tags_to_snapshot       = true
  deletion_protection         = true
  skip_final_snapshot         = false
  final_snapshot_identifier   = "${var.name}-final"
  auto_minor_version_upgrade  = true
  tags                        = local.tags
  lifecycle {
    prevent_destroy = true
  }
}
output "instance_name" { value = aws_db_instance.app.identifier }
output "host" { value = aws_db_instance.app.address }
output "port" { value = aws_db_instance.app.port }
output "admin_secret_id" { value = aws_db_instance.app.master_user_secret[0].secret_arn }
