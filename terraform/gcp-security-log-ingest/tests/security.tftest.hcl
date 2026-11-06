mock_provider "aws" {
  mock_resource "aws_iam_role" { defaults = { arn = "arn:aws:iam::482840608926:role/delta-security-gcp-logs" } }
  mock_resource "aws_cloudwatch_log_group" { defaults = { arn = "arn:aws:logs:ap-northeast-2:482840608926:log-group:test" } }
  mock_resource "aws_lambda_function" { defaults = { arn = "arn:aws:lambda:ap-northeast-2:482840608926:function:test" } }
  mock_resource "aws_cloudwatch_event_rule" { defaults = { arn = "arn:aws:events:ap-northeast-2:482840608926:rule/test" } }
}
mock_provider "google" {
  mock_data "google_project" { defaults = { number = "366844544284" } }
  mock_resource "google_logging_project_sink" { defaults = { writer_identity = "serviceAccount:security@term2-delta.iam.gserviceaccount.com" } }
}
variables { collector_zip = "tests/security.tftest.hcl" }
run "bounded_security_collector" {
  command = apply
  assert {
    condition = (
      length(aws_cloudwatch_log_group.source) == 2 &&
      alltrue([for group in values(aws_cloudwatch_log_group.source) : group.skip_destroy && group.retention_in_days == 30 && group.log_group_class == "STANDARD"]) &&
      alltrue([for filter in values(aws_cloudwatch_log_subscription_filter.platform) : filter.name == "delta-platform-central" && filter.filter_pattern == "" && filter.destination_arn == "arn:aws:logs:ap-northeast-2:616723161368:destination:delta-platform-logs"])
    )
    error_message = "Keep two retained Standard sources and explicit platform subscriptions."
  }
  assert {
    condition = (
      length(jsondecode(aws_lambda_function.collector.environment[0].variables.DESTINATION_LOG_GROUPS)) == 2 &&
      strcontains(google_iam_workload_identity_pool_provider.collector.attribute_condition, "482840608926:assumed-role/delta-security-gcp-logs/") &&
      google_pubsub_subscription.logs.dead_letter_policy[0].max_delivery_attempts == 10 &&
      google_pubsub_subscription.failed.message_retention_duration == "604800s" &&
      google_logging_project_sink.security.filter == "logName=(\"projects/term2-delta/logs/compute.googleapis.com%2Ffirewall\" OR \"projects/term2-delta/logs/networksecurity.googleapis.com%2Ffirewall_threat\")"
    )
    error_message = "Limit the source filter and WIF identity and retain failed messages."
  }
}
run "pause_keeps_groups_and_queues" {
  command = plan
  variables {
    forwarding_enabled = false
    collector_enabled  = false
    sink_enabled       = false
  }
  assert {
    condition     = length(aws_cloudwatch_log_group.source) == 2 && length(aws_cloudwatch_log_subscription_filter.platform) == 0 && google_logging_project_sink.security.disabled && aws_cloudwatch_event_rule.poll.state == "DISABLED"
    error_message = "Pause transport without removing original storage or queues."
  }
}
run "reject_wrong_security_account" {
  command = plan
  variables { security_account_id = "616723161368" }
  expect_failures = [var.security_account_id]
}
