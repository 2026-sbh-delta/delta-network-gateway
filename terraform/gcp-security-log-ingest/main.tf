data "google_project" "source" { project_id = var.gcp_project_id }

locals {
  source_groups = {
    "compute.googleapis.com%2Ffirewall"                = "/delta/security/gcp-firewall"
    "networksecurity.googleapis.com%2Ffirewall_threat" = "/delta/security/gcp-firewall-threat"
  }
  routes          = { for name, group in local.source_groups : "projects/${var.gcp_project_id}/logs/${name}" => group }
  destination_arn = "arn:aws:logs:ap-northeast-2:616723161368:destination:delta-platform-logs"
  service_agent   = "serviceAccount:service-${data.google_project.source.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
  principal       = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.collector.name}/attribute.aws_role/${aws_iam_role.collector.name}"
}

resource "aws_cloudwatch_log_group" "source" {
  for_each          = local.source_groups
  name              = each.value
  log_group_class   = "STANDARD"
  retention_in_days = var.retention_days
  skip_destroy      = true
}
resource "aws_cloudwatch_log_group" "runtime" {
  name              = "/aws/lambda/${var.name}"
  retention_in_days = var.retention_days
  skip_destroy      = true
}
resource "aws_cloudwatch_log_subscription_filter" "platform" {
  for_each        = var.forwarding_enabled ? local.source_groups : {}
  name            = "delta-platform-central"
  log_group_name  = aws_cloudwatch_log_group.source[each.key].name
  filter_pattern  = ""
  distribution    = "Random"
  destination_arn = local.destination_arn
}
resource "aws_iam_role" "collector" {
  name = var.name
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole"
  }] })
}
resource "aws_iam_role_policy" "write" {
  name = "write-security-log-groups"
  role = aws_iam_role.collector.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect   = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"],
    Resource = concat([for group in values(aws_cloudwatch_log_group.source) : "${group.arn}:*"], ["${aws_cloudwatch_log_group.runtime.arn}:*"])
  }] })
}

resource "google_pubsub_topic" "logs" { name = var.name }
resource "google_pubsub_topic" "failed" { name = "${var.name}-dlq" }
resource "google_pubsub_subscription" "logs" {
  name                       = "${var.name}-aws"
  topic                      = google_pubsub_topic.logs.id
  ack_deadline_seconds       = 60
  message_retention_duration = "86400s"
  expiration_policy { ttl = "" }
  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "60s"
  }
  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.failed.id
    max_delivery_attempts = 10
  }
}
resource "google_pubsub_subscription" "failed" {
  name                       = "${var.name}-dlq"
  topic                      = google_pubsub_topic.failed.id
  message_retention_duration = "604800s"
  expiration_policy { ttl = "" }
}
resource "google_logging_project_sink" "security" {
  name                   = var.name
  destination            = "pubsub.googleapis.com/${google_pubsub_topic.logs.id}"
  unique_writer_identity = true
  filter                 = "logName=(${join(" OR ", [for name in sort(keys(local.routes)) : jsonencode(name)])})"
  disabled               = !var.sink_enabled
  lifecycle {
    precondition {
      condition     = data.google_project.source.number == "366844544284"
      error_message = "The source project number must match term2-delta."
    }
  }
}
resource "google_pubsub_topic_iam_member" "sink" {
  topic  = google_pubsub_topic.logs.name
  role   = "roles/pubsub.publisher"
  member = google_logging_project_sink.security.writer_identity
}
resource "google_pubsub_topic_iam_member" "failed" {
  topic  = google_pubsub_topic.failed.name
  role   = "roles/pubsub.publisher"
  member = local.service_agent
}
resource "google_pubsub_subscription_iam_member" "forwarder" {
  subscription = google_pubsub_subscription.logs.name
  role         = "roles/pubsub.subscriber"
  member       = local.service_agent
}
resource "google_iam_workload_identity_pool" "collector" {
  workload_identity_pool_id = "${var.name}-aws"
  display_name              = "Security GCP firewall collector"
}
resource "google_iam_workload_identity_pool_provider" "collector" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.collector.workload_identity_pool_id
  workload_identity_pool_provider_id = "aws-collector"
  attribute_mapping = {
    "google.subject"     = "assertion.arn"
    "attribute.aws_role" = "assertion.arn.extract('assumed-role/{role}/')"
  }
  attribute_condition = "assertion.account == '${var.security_account_id}' && assertion.arn.startsWith('arn:aws:sts::${var.security_account_id}:assumed-role/${aws_iam_role.collector.name}/')"
  aws { account_id = var.security_account_id }
}
resource "google_pubsub_subscription_iam_member" "collector" {
  subscription = google_pubsub_subscription.logs.name
  role         = "roles/pubsub.subscriber"
  member       = local.principal
}
resource "aws_lambda_function" "collector" {
  function_name    = var.name
  role             = aws_iam_role.collector.arn
  handler          = "collector.handler"
  runtime          = "python3.13"
  architectures    = ["x86_64"]
  memory_size      = 256
  timeout          = 50
  filename         = var.collector_zip
  source_code_hash = filebase64sha256(var.collector_zip)
  environment {
    variables = {
      PUBSUB_SUBSCRIPTION    = google_pubsub_subscription.logs.id
      WIF_AUDIENCE           = "//iam.googleapis.com/${google_iam_workload_identity_pool_provider.collector.name}"
      DESTINATION_LOG_GROUPS = jsonencode(local.routes)
      DESTINATION_LOG_STREAM = "gcp-firewall"
    }
  }
  depends_on = [aws_iam_role_policy.write, google_pubsub_subscription_iam_member.collector, google_pubsub_subscription_iam_member.forwarder, google_pubsub_topic_iam_member.failed]
}
resource "aws_cloudwatch_event_rule" "poll" {
  name                = "${var.name}-poll"
  schedule_expression = "rate(1 minute)"
  state               = var.collector_enabled ? "ENABLED" : "DISABLED"
}
resource "aws_cloudwatch_event_target" "poll" {
  rule = aws_cloudwatch_event_rule.poll.name
  arn  = aws_lambda_function.collector.arn
  retry_policy {
    maximum_event_age_in_seconds = 60
    maximum_retry_attempts       = 0
  }
}
resource "aws_lambda_permission" "poll" {
  statement_id  = "ScheduledPull"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.collector.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.poll.arn
}
resource "aws_lambda_function_event_invoke_config" "collector" {
  function_name                = aws_lambda_function.collector.function_name
  maximum_event_age_in_seconds = 60
  maximum_retry_attempts       = 0
}
resource "aws_cloudwatch_metric_alarm" "errors" {
  alarm_name          = "${var.name}-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = { FunctionName = aws_lambda_function.collector.function_name }
}
