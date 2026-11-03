output "runtime_env" {
  description = "기존 SSM app_env에 병합할 공개 실행기 설정"
  value = {
    PLATFORM_SANDBOX_TARGET = jsonencode({
      cloud                  = "aws", region = var.region, account_id = var.account_id,
      cluster                = aws_ecs_cluster.sandbox.name, service = aws_ecs_service.sandbox.name,
      task_family            = aws_ecs_task_definition.sandbox.family, container_name = "app",
      target_group_arn       = aws_lb_target_group.sandbox.arn,
      url                    = "http://${aws_lb.sandbox.dns_name}", dynamic_scanner_access = false,
      allowed_image_prefixes = ["${data.aws_ecr_repository.customer.repository_url}@"]
    })
  }
}
output "network" {
  description = "팀 네트워크 배정 확인용 대역과 태스크 서브넷"
  value = {
    vpc_id         = data.aws_vpc.platform.id, vpc_cidr = data.aws_vpc.platform.cidr_block,
    alb_cidr       = aws_subnet.alb.cidr_block, task_cidr = aws_subnet.task.cidr_block,
    task_subnet_id = aws_subnet.task.id
  }
}
