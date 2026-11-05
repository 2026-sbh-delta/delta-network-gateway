mock_provider "aws" {
  override_during = plan
  mock_data "aws_caller_identity" { defaults = { account_id = "111122223333" } }
  mock_resource "aws_iam_role" { defaults = { arn = "arn:aws:iam::111122223333:role/delta-lab-gcp-log-collector" } }
  mock_resource "aws_cloudwatch_log_group" { defaults = { arn = "arn:aws:logs:ap-northeast-2:111122223333:log-group:mock" } }
}
run "keyless_bounded_collector" {
  command = plan
  assert {
    condition     = aws_lambda_function.collector.runtime == "python3.13" && aws_lambda_function.collector.timeout == 50 && aws_lambda_function.collector.reserved_concurrent_executions == -1 && length(aws_lambda_function.collector.vpc_config) == 0 && aws_cloudwatch_event_rule.poll.schedule_expression == "rate(1 minute)"
    error_message = "Use bounded polling without an unsupported reservation, independent of the workload VPN."
  }
  assert {
    condition     = aws_cloudwatch_event_target.poll.retry_policy[0].maximum_retry_attempts == 0 && aws_lambda_function_event_invoke_config.poll.maximum_retry_attempts == 0 && aws_lambda_function_event_invoke_config.poll.maximum_event_age_in_seconds == 60
    error_message = "Retry original Pub/Sub messages on the next poll rather than queueing duplicate stale Lambda invocations."
  }
  assert {
    condition     = toset(keys(aws_lambda_function.collector.environment[0].variables)) == toset(["PUBSUB_SUBSCRIPTION", "WIF_AUDIENCE", "DESTINATION_LOG_GROUP"]) && aws_cloudwatch_log_group.gcp.skip_destroy && aws_cloudwatch_log_group.gcp.retention_in_days == 30
    error_message = "No static credentials may enter the runtime configuration, and original logs must be retained."
  }
  assert {
    condition     = aws_lambda_permission.poll.source_account == "111122223333" && aws_lambda_permission.poll.principal == "events.amazonaws.com" && !strcontains(aws_iam_role_policy.collector.policy, "network-firewall")
    error_message = "Invocation is central-account scoped and collector permissions must not modify ANF."
  }
}
run "wrong_aws_account" {
  command = plan
  variables { central_account_id = "999999999999" }
  expect_failures = [terraform_data.contract]
}
run "wrong_gcp_subscription" {
  command = plan
  variables { subscription = "projects/other/subscriptions/demo" }
  expect_failures = [var.subscription]
}
