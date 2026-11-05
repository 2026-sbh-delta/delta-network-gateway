data "google_project" "source" { project_id = var.gcp_project_id }
locals {
  gcp_filter = coalesce(var.gcp_log_filter, <<-FILTER
    NOT logName:"cloudaudit.googleapis.com"
    AND (
      resource.type="gce_instance"
      OR resource.type="cloud_run_revision"
      OR logName=("projects/${var.gcp_project_id}/logs/compute.googleapis.com%2Ffirewall"
                  OR "projects/${var.gcp_project_id}/logs/networksecurity.googleapis.com%2Ffirewall_threat"
                  OR "projects/${var.gcp_project_id}/logs/compute.googleapis.com%2Fvpn"
                  OR "projects/${var.gcp_project_id}/logs/compute.googleapis.com%2Frouter"
                  OR "projects/${var.gcp_project_id}/logs/delta-platform-log-smoke")
    )
  FILTER
  )
  pubsub_service_agent = "serviceAccount:service-${data.google_project.source.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
  gcp_principal        = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.aws.name}/attribute.aws_role/${aws_iam_role.collector["gcp"].name}"
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
resource "google_logging_project_sink" "logs" {
  name                   = var.name
  destination            = "pubsub.googleapis.com/${google_pubsub_topic.logs.id}"
  unique_writer_identity = true
  filter                 = local.gcp_filter
}
resource "google_pubsub_topic_iam_member" "sink" {
  topic  = google_pubsub_topic.logs.name
  role   = "roles/pubsub.publisher"
  member = google_logging_project_sink.logs.writer_identity
}
resource "google_pubsub_topic_iam_member" "failed" {
  topic  = google_pubsub_topic.failed.name
  role   = "roles/pubsub.publisher"
  member = local.pubsub_service_agent
}
resource "google_pubsub_subscription_iam_member" "forwarder" {
  subscription = google_pubsub_subscription.logs.name
  role         = "roles/pubsub.subscriber"
  member       = local.pubsub_service_agent
}
resource "google_iam_workload_identity_pool" "aws" {
  workload_identity_pool_id = "${var.name}-aws"
  display_name              = "Platform log collector"
}
resource "google_iam_workload_identity_pool_provider" "aws" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.aws.workload_identity_pool_id
  workload_identity_pool_provider_id = "aws-collector"
  attribute_mapping = {
    "google.subject"     = "assertion.arn"
    "attribute.aws_role" = "assertion.arn.extract('assumed-role/{role}/')"
  }
  attribute_condition = "assertion.account == '${var.aws_account_id}' && assertion.arn.startsWith('arn:aws:sts::${var.aws_account_id}:assumed-role/${aws_iam_role.collector["gcp"].name}/')"
  aws { account_id = var.aws_account_id }
}
resource "google_pubsub_subscription_iam_member" "collector" {
  subscription = google_pubsub_subscription.logs.name
  role         = "roles/pubsub.subscriber"
  member       = local.gcp_principal
}
resource "aws_lambda_function" "gcp" {
  function_name    = local.functions.gcp
  role             = aws_iam_role.collector["gcp"].arn
  handler          = "collector.handler"
  runtime          = "python3.13"
  architectures    = ["x86_64"]
  memory_size      = 256
  timeout          = 50
  filename         = var.gcp_lambda_zip
  source_code_hash = filebase64sha256(var.gcp_lambda_zip)
  environment {
    variables = {
      PUBSUB_SUBSCRIPTION    = google_pubsub_subscription.logs.id
      WIF_AUDIENCE           = "//iam.googleapis.com/${google_iam_workload_identity_pool_provider.aws.name}"
      DESTINATION_LOG_GROUP  = aws_cloudwatch_log_group.central["gcp"].name
      DESTINATION_LOG_STREAM = "gcp-cloud-logging"
    }
  }
  depends_on = [aws_iam_role_policy.logs, google_pubsub_subscription_iam_member.collector, google_pubsub_subscription_iam_member.forwarder, google_pubsub_topic_iam_member.failed]
}
resource "aws_cloudwatch_event_rule" "gcp" {
  name                = "${var.name}-gcp"
  schedule_expression = "rate(1 minute)"
}
resource "aws_cloudwatch_event_target" "gcp" {
  rule = aws_cloudwatch_event_rule.gcp.name
  arn  = aws_lambda_function.gcp.arn
  retry_policy {
    maximum_event_age_in_seconds = 60
    maximum_retry_attempts       = 0
  }
}
resource "aws_lambda_permission" "gcp" {
  statement_id  = "ScheduledPull"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.gcp.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.gcp.arn
}
resource "aws_lambda_function_event_invoke_config" "gcp" {
  function_name                = aws_lambda_function.gcp.function_name
  maximum_event_age_in_seconds = 60
  maximum_retry_attempts       = 0
}
