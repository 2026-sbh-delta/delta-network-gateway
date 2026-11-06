output "source_log_groups" { value = { for name, group in aws_cloudwatch_log_group.source : name => group.name } }
output "platform_destination_arn" { value = local.destination_arn }
output "subscription_filter_names" { value = { for name in keys(local.source_groups) : name => try(aws_cloudwatch_log_subscription_filter.platform[name].name, null) } }
output "collector_function" { value = aws_lambda_function.collector.function_name }
output "poll_rule" { value = aws_cloudwatch_event_rule.poll.name }
output "gcp_sink" { value = google_logging_project_sink.security.name }
output "gcp_subscription" { value = google_pubsub_subscription.logs.id }
output "gcp_failed_subscription" { value = google_pubsub_subscription.failed.id }
output "wif_audience" { value = "//iam.googleapis.com/${google_iam_workload_identity_pool_provider.collector.name}" }
