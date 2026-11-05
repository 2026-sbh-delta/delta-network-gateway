resource "aws_ecs_cluster" "staging" {
  name = local.prefix
  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}

resource "aws_ecr_repository" "app" {
  count                = var.create_ecr_repository ? 1 : 0
  name                 = local.prefix
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }
  encryption_configuration {
    encryption_type = "AES256"
  }
}
resource "aws_ecr_lifecycle_policy" "app" {
  count      = var.create_ecr_repository ? 1 : 0
  repository = aws_ecr_repository.app[0].name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the latest 20 staging images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 20 }
      action       = { type = "expire" }
    }]
  })
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/delta/${local.prefix}"
  retention_in_days = var.log_retention_days
}

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
  }
}
resource "aws_iam_role" "execution" {
  name_prefix        = "${local.prefix}-exec-"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}
resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}
# The app calls no AWS API. A role without policies keeps task credentials empty.
resource "aws_iam_role" "task" {
  name_prefix        = "${local.prefix}-task-"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_ecs_task_definition" "app" {
  family                   = local.prefix
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.cpu)
  memory                   = tostring(var.memory)
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }
  container_definitions = jsonencode(concat([merge({
    name         = local.ingress_container_name
    image        = var.bootstrap_image
    essential    = true
    portMappings = [{ containerPort = local.ingress_container_port, protocol = "tcp" }]
    environment = var.dual_container ? concat(local.app_environment, [
      { name = "API_HOST", value = "127.0.0.1" },
      { name = "API_PORT", value = "8080" },
      { name = "PORT", value = "3000" }
    ]) : []
    logConfiguration = local.app_logs
    }, var.dual_container ? {
    dependsOn = [{ containerName = "backend", condition = "HEALTHY" }]
    } : {})], var.dual_container ? [{
    name         = "backend"
    image        = var.bootstrap_backend_image
    essential    = true
    portMappings = [{ containerPort = 8080, protocol = "tcp" }]
    environment = concat(local.app_environment, local.database_environment, [
      { name = "PORT", value = "8080" }
    ])
    secrets = local.runtime_database_secrets
    healthCheck = {
      command     = ["CMD", "node", "-e", "fetch('http://127.0.0.1:8080/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
      interval    = 5
      timeout     = 3
      retries     = 3
      startPeriod = 10
    }
    logConfiguration = local.app_logs
  }] : []))
}

resource "aws_lb" "staging" {
  name                       = substr("${local.prefix}-alb", 0, 32)
  internal                   = !var.alb_internet_facing
  load_balancer_type         = "application"
  subnets                    = sort(tolist(var.subnet_ids))
  security_groups            = [aws_security_group.alb.id]
  drop_invalid_header_fields = true
  idle_timeout               = 60
}
resource "aws_lb_target_group" "app" {
  name_prefix          = "stg-"
  port                 = local.ingress_container_port
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = var.vpc_id
  deregistration_delay = 5
  health_check {
    path                = var.health_check_path
    matcher             = "200-399"
    interval            = 5
    timeout             = 4
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
  lifecycle {
    create_before_destroy = true
  }
}
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.staging.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}

# The staging tool swaps the image with a new task definition revision per run. Terraform owns only the base.
# min 0 / max 200: on a swap ECS starts the new task (image pull included) while the old one stops, instead of
# waiting for the old task to finish stopping first. The tool reports ready only once the new task alone is healthy.
resource "aws_ecs_service" "app" {
  name                               = local.prefix
  cluster                            = aws_ecs_cluster.staging.id
  task_definition                    = aws_ecs_task_definition.app.arn
  desired_count                      = 0
  launch_type                        = "FARGATE"
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 200
  enable_execute_command             = false
  network_configuration {
    subnets          = sort(tolist(var.subnet_ids))
    security_groups  = [aws_security_group.service.id]
    assign_public_ip = false
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = local.ingress_container_name
    container_port   = local.ingress_container_port
  }
  deployment_circuit_breaker {
    enable   = true
    rollback = false
  }
  lifecycle {
    ignore_changes = [task_definition, desired_count]
  }
  depends_on = [aws_lb_listener.http, aws_iam_role_policy_attachment.execution]
}
