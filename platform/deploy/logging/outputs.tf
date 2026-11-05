output "aws_destination_arn" { value = aws_cloudwatch_log_destination.aws.arn }
output "aws_region" { value = var.aws_region }
output "allowed_source_accounts" { value = sort(tolist(var.source_aws_accounts)) }
output "central_log_groups" { value = { for k, v in aws_cloudwatch_log_group.central : k => v.name } }
output "collector_functions" { value = local.functions }
output "aws_failure_bucket" { value = aws_s3_bucket.failed.id }
output "gcp_sink" { value = google_logging_project_sink.logs.name }
output "gcp_topic" { value = google_pubsub_topic.logs.id }
output "gcp_subscription" { value = google_pubsub_subscription.logs.id }
output "gcp_failed_subscription" { value = google_pubsub_subscription.failed.id }
output "gcp_log_filter" { value = google_logging_project_sink.logs.filter }
output "gcp_wif_audience" { value = "//iam.googleapis.com/${google_iam_workload_identity_pool_provider.aws.name}" }
