data "aws_ecr_repository" "customer" { name = "delta-platform-builds" }
resource "aws_cloudwatch_log_group" "sandbox" {
  name              = "/ecs/${local.name}"
  retention_in_days = 7
}
resource "aws_ecs_cluster" "sandbox" { name = local.name }
resource "aws_ecs_task_definition" "sandbox" {
  family                   = local.name
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "512"
  task_role_arn            = aws_iam_role.task.arn
  execution_role_arn       = aws_iam_role.execution.arn
  skip_destroy             = true
  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }
  container_definitions = jsonencode([{
    name            = "app", image = "${data.aws_ecr_repository.customer.repository_url}@${var.bootstrap_image_digest}", essential = true,
    portMappings    = [{ containerPort = 3000, protocol = "tcp" }],
    linuxParameters = { capabilities = { add = [], drop = ["NET_RAW"] } },
    logConfiguration = { logDriver = "awslogs", options = {
      awslogs-group = aws_cloudwatch_log_group.sandbox.name, awslogs-region = var.region, awslogs-stream-prefix = "app"
    } }
  }])
}
resource "aws_ecs_service" "sandbox" {
  name                               = local.name
  cluster                            = aws_ecs_cluster.sandbox.id
  task_definition                    = aws_ecs_task_definition.sandbox.arn
  desired_count                      = 0
  launch_type                        = "FARGATE"
  enable_execute_command             = false
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100
  health_check_grace_period_seconds  = 30
  deployment_circuit_breaker {
    enable   = true
    rollback = false
  }
  network_configuration {
    subnets          = [aws_subnet.task.id]
    security_groups  = [aws_security_group.task.id]
    assign_public_ip = false
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.sandbox.arn
    container_name   = "app"
    container_port   = 3000
  }
  lifecycle { ignore_changes = [task_definition, desired_count, load_balancer] }
  depends_on = [aws_lb_listener.sandbox, aws_iam_role_policy.execution, aws_route.nat, aws_vpc_security_group_ingress_rule.nat_https]
}
