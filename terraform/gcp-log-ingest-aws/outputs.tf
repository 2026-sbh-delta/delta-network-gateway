output "collector_role_arn" {
  description = "Exact IAM role to trust in GCP WIF."
  value       = aws_iam_role.collector.arn
}
output "log_group" {
  description = "GCP original log destination."
  value       = aws_cloudwatch_log_group.gcp.name
}
output "collector_log_group" {
  description = "Collector execution logs and ingest counters."
  value       = aws_cloudwatch_log_group.collector.name
}
output "function_name" {
  description = "Lambda function for manual health checks."
  value       = aws_lambda_function.collector.function_name
}
output "poll_rule_name" {
  description = "EventBridge poll rule; disable this to stop pulling while retaining messages."
  value       = aws_cloudwatch_event_rule.poll.name
}
