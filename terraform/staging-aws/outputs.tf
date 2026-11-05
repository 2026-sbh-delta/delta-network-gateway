output "region" {
  description = "Staging Region."
  value       = var.region
}
output "cluster_name" {
  description = "ECS cluster name."
  value       = aws_ecs_cluster.staging.name
}
output "service_name" {
  description = "ECS service the staging tool updates."
  value       = aws_ecs_service.app.name
}
output "task_family" {
  description = "Task definition family the staging tool revises."
  value       = aws_ecs_task_definition.app.family
}
output "target_group_arn" {
  description = "Target group whose health the staging tool waits for."
  value       = aws_lb_target_group.app.arn
}
output "internal_url" {
  description = "Staging URL reachable only from scanner sources."
  value       = "http://${aws_lb.staging.dns_name}"
}
output "ecr_repository_url" {
  description = "ECR repository for staging images, or null when not created."
  value       = try(aws_ecr_repository.app[0].repository_url, null)
}
output "alb_security_group_id" {
  description = "Staging ALB security group."
  value       = aws_security_group.alb.id
}
output "log_group_name" {
  description = "CloudWatch log group of staging tasks."
  value       = aws_cloudwatch_log_group.app.name
}
output "tool_config" {
  description = "Target entry for the staging tool config file (aws)."
  value = {
    cloud                   = "aws"
    environment             = "staging"
    reset_task_family       = var.dual_container ? aws_ecs_task_definition.db_reset[0].family : null
    backend_container_name  = var.dual_container ? "backend" : null
    expected_database_major = 18
    database_name           = local.database_name
    schema_prepared         = var.schema_prepared
    reset_container_name    = "reset"
    region                  = var.region
    account_id              = var.account_id
    cluster                 = aws_ecs_cluster.staging.name
    service                 = aws_ecs_service.app.name
    task_family             = aws_ecs_task_definition.app.family
    container_name          = local.ingress_container_name
    target_group_arn        = aws_lb_target_group.app.arn
    alb_security_group_id   = aws_security_group.alb.id
    dynamic_scanner_access  = var.alb_internet_facing
    allowed_image_prefixes  = local.allowed_image_prefixes
  }
}

output "operator_handoff" {
  description = "Non-secret DB and delegated IAM references (no manual provisioning step); never a tool target entry."
  value = var.dual_container ? {
    database_host              = aws_db_instance.app[0].address
    database_name              = local.database_name
    runtime_secret_arn         = aws_secretsmanager_secret.runtime[0].arn
    runtime_execution_role_arn = aws_iam_role.execution.arn
    runtime_task_role_arn      = aws_iam_role.task.arn
    cluster_arn                = aws_ecs_cluster.staging.arn
    service_arn                = aws_ecs_service.app.id
    reset_task_definition_arn  = aws_ecs_task_definition.db_reset[0].arn
    reset_log_stream_arn       = "arn:aws:logs:${var.region}:${var.account_id}:log-group:${aws_cloudwatch_log_group.app.name}:log-stream:reset/reset/*"
  } : null
}
