provider "aws" {
  region              = var.region
  allowed_account_ids = [var.central_account_id]
  default_tags { tags = { Team = "Delta", Environment = "lab", ManagedBy = "Terraform" } }
}
data "aws_caller_identity" "current" {}
resource "terraform_data" "contract" {
  input = "delta-gcp-log-ingest-v1"
  lifecycle {
    precondition {
      condition     = data.aws_caller_identity.current.account_id == var.central_account_id
      error_message = "Authenticated AWS account is not the expected central account."
    }
  }
}
locals { function_name = "${var.name}-gcp-log-collector" }
resource "aws_cloudwatch_log_group" "gcp" {
  name              = "/delta/lab/gcp/term2-delta/network-security"
  retention_in_days = var.log_retention_days
  skip_destroy      = true
  depends_on        = [terraform_data.contract]
}
resource "aws_cloudwatch_log_group" "collector" {
  name              = "/aws/lambda/${local.function_name}"
  retention_in_days = var.log_retention_days
  skip_destroy      = true
  depends_on        = [terraform_data.contract]
}
resource "aws_iam_role" "collector" {
  name               = local.function_name
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }] })
  depends_on         = [terraform_data.contract]
}
resource "aws_iam_role_policy" "collector" {
  name = "write-gcp-and-runtime-logs"
  role = aws_iam_role.collector.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = ["${aws_cloudwatch_log_group.gcp.arn}:log-stream:*", "${aws_cloudwatch_log_group.collector.arn}:log-stream:*"]
    }]
  })
}
resource "aws_lambda_function" "collector" {
  function_name    = local.function_name
  role             = aws_iam_role.collector.arn
  runtime          = "python3.13"
  architectures    = ["x86_64"]
  handler          = "collector.handler"
  filename         = var.collector_zip
  source_code_hash = filebase64sha256(var.collector_zip)
  timeout          = 50
  memory_size      = 256
  # This account has a regional concurrency quota of 10. Reservations require
  # 100 unreserved slots, so use bounded polling without a reservation.
  reserved_concurrent_executions = -1
  environment {
    variables = {
      PUBSUB_SUBSCRIPTION   = var.subscription
      WIF_AUDIENCE          = var.wif_audience
      DESTINATION_LOG_GROUP = aws_cloudwatch_log_group.gcp.name
    }
  }
  depends_on = [aws_iam_role_policy.collector]
}
resource "aws_cloudwatch_event_rule" "poll" {
  name                = "${local.function_name}-poll"
  schedule_expression = "rate(1 minute)"
  depends_on          = [terraform_data.contract]
}
resource "aws_lambda_permission" "poll" {
  statement_id   = "CentralEventBridgeOnly"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.collector.function_name
  principal      = "events.amazonaws.com"
  source_arn     = aws_cloudwatch_event_rule.poll.arn
  source_account = var.central_account_id
}
resource "aws_cloudwatch_event_target" "poll" {
  rule = aws_cloudwatch_event_rule.poll.name
  arn  = aws_lambda_function.collector.arn
  retry_policy {
    maximum_event_age_in_seconds = 60
    maximum_retry_attempts       = 0
  }
  depends_on = [aws_lambda_permission.poll]
}

resource "aws_lambda_function_event_invoke_config" "poll" {
  function_name                = aws_lambda_function.collector.function_name
  maximum_event_age_in_seconds = 60
  maximum_retry_attempts       = 0
}
resource "aws_cloudwatch_metric_alarm" "errors" {
  alarm_name          = "${local.function_name}-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = local.function_name }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_description   = "GCP log pull/store/ack failure; inspect execution logs, subscription backlog and DLQ."
  depends_on          = [terraform_data.contract]
}
