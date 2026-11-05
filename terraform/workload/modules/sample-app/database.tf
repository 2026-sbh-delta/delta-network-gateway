resource "aws_db_subnet_group" "app" {
  name       = local.prefix
  subnet_ids = [for id in sort(keys(aws_subnet.database)) : aws_subnet.database[id].id]
}
resource "aws_db_parameter_group" "app" {
  name_prefix = "${local.prefix}-"
  family      = "postgres${split(".", var.config.db_engine_version)[0]}"
  parameter {
    name         = "rds.force_ssl"
    value        = "1"
    apply_method = "pending-reboot"
  }
  lifecycle { create_before_destroy = true }
}
resource "aws_db_instance" "app" {
  identifier                      = local.prefix
  engine                          = "postgres"
  engine_version                  = var.config.db_engine_version
  instance_class                  = var.config.db_instance_class
  allocated_storage               = var.config.db_allocated_storage
  storage_type                    = "gp3"
  storage_encrypted               = true
  db_name                         = "delta_notes"
  username                        = "delta_admin"
  manage_master_user_password     = true
  db_subnet_group_name            = aws_db_subnet_group.app.name
  parameter_group_name            = aws_db_parameter_group.app.name
  vpc_security_group_ids          = [aws_security_group.app["database"].id]
  availability_zone               = var.workload_subnets[var.instance_az_id].az_name
  publicly_accessible             = false
  multi_az                        = false
  backup_retention_period         = 7
  deletion_protection             = true
  skip_final_snapshot             = false
  final_snapshot_identifier       = "${local.prefix}-final"
  auto_minor_version_upgrade      = false
  copy_tags_to_snapshot           = true
  enabled_cloudwatch_logs_exports = ["postgresql"]
  depends_on                      = [terraform_data.contract, aws_route_table_association.database]
}
resource "aws_secretsmanager_secret" "app" {
  name                    = "${local.prefix}/database/app"
  description             = "Application-only DB credential; value is provisioned outside Terraform"
  recovery_window_in_days = 7
}
