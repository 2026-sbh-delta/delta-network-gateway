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
    cloud                  = "aws"
    region                 = var.region
    account_id             = var.account_id
    cluster                = aws_ecs_cluster.staging.name
    service                = aws_ecs_service.app.name
    task_family            = aws_ecs_task_definition.app.family
    container_name         = var.container_name
    target_group_arn       = aws_lb_target_group.app.arn
    alb_security_group_id  = aws_security_group.alb.id
    dynamic_scanner_access = var.alb_internet_facing
    url                    = "http://${aws_lb.staging.dns_name}"
    allowed_image_prefixes = local.allowed_image_prefixes
  }
}
