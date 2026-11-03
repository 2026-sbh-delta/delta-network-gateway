locals {
  ecs = "arn:aws:ecs:${var.region}:${var.account_id}"
  ecs_trust = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Action = "sts:AssumeRole", Principal = { Service = "ecs-tasks.amazonaws.com" },
    Condition = { StringEquals = { "aws:SourceAccount" = var.account_id }, ArnLike = { "aws:SourceArn" = "${local.ecs}:*" } }
  }] })
}
resource "aws_iam_role" "task" {
  name               = "${local.name}-task"
  assume_role_policy = local.ecs_trust
}
resource "aws_iam_role" "execution" {
  name               = "${local.name}-execution"
  assume_role_policy = local.ecs_trust
}
resource "aws_iam_role_policy" "execution" {
  name = "sandbox-image-and-logs"
  role = aws_iam_role.execution.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Effect = "Allow", Action = ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"], Resource = data.aws_ecr_repository.customer.arn },
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.sandbox.arn}:*" }
  ] })
}
resource "aws_iam_role_policy" "platform_sandbox" {
  name = "sandbox-runtime-check"
  role = "delta-platform-app"
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ecs:DescribeServices", "ecs:UpdateService"], Resource = "${local.ecs}:service/${local.name}/${local.name}" },
    # These read-only APIs do not support resource-level authorization in AWS.
    { Effect = "Allow", Action = ["ecs:DescribeTaskDefinition", "elasticloadbalancing:DescribeTargetHealth"], Resource = "*" },
    { Effect = "Allow", Action = ["ecs:RegisterTaskDefinition"], Resource = "${local.ecs}:task-definition/${local.name}:*" },
    { Effect = "Allow", Action = ["ecs:TagResource"], Resource = "${local.ecs}:task-definition/${local.name}:*", Condition = { StringEquals = { "ecs:CreateAction" = "RegisterTaskDefinition" } } },
    { Effect = "Allow", Action = ["ecs:ListTasks"], Resource = "*", Condition = { ArnEquals = { "ecs:cluster" = aws_ecs_cluster.sandbox.arn } } },
    { Effect = "Allow", Action = ["ecs:DescribeTasks"], Resource = "${local.ecs}:task/${local.name}/*" },
    { Effect = "Allow", Action = ["elasticloadbalancing:ModifyTargetGroup"], Resource = aws_lb_target_group.sandbox.arn },
    { Effect = "Allow", Action = ["iam:PassRole"], Resource = [aws_iam_role.task.arn, aws_iam_role.execution.arn], Condition = { StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" } } }
  ] })
}
