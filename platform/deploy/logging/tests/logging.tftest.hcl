mock_provider "aws" {
  mock_resource "aws_iam_role" { defaults = { arn = "arn:aws:iam::111122223333:role/test" } }
  mock_resource "aws_kinesis_stream" { defaults = { arn = "arn:aws:kinesis:ap-northeast-2:111122223333:stream/test" } }
  mock_resource "aws_cloudwatch_log_group" { defaults = { arn = "arn:aws:logs:ap-northeast-2:111122223333:log-group:test" } }
  mock_resource "aws_cloudwatch_log_destination" { defaults = { arn = "arn:aws:logs:ap-northeast-2:111122223333:destination:test" } }
  mock_resource "aws_s3_bucket" { defaults = { arn = "arn:aws:s3:::test-failure-bucket" } }
  mock_resource "aws_lambda_function" { defaults = { arn = "arn:aws:lambda:ap-northeast-2:111122223333:function:test" } }
  mock_resource "aws_cloudwatch_event_rule" { defaults = { arn = "arn:aws:events:ap-northeast-2:111122223333:rule/test" } }
}
mock_provider "google" {
  mock_data "google_project" { defaults = { number = "366844544284" } }
  mock_resource "google_logging_project_sink" { defaults = { writer_identity = "serviceAccount:logs@term2-delta.iam.gserviceaccount.com" } }
}
variables {
  aws_account_id      = "111122223333"
  source_aws_accounts = ["444455556666"]
  aws_lambda_zip      = "tests/logging.tftest.hcl"
  gcp_lambda_zip      = "tests/logging.tftest.hcl"
}
run "bounded_private_ingestion" {
  command = apply
  assert {
    condition     = aws_kinesis_stream.aws.shard_count == 1 && aws_kinesis_stream.aws.encryption_type == "KMS" && aws_kinesis_stream.aws.retention_period == 24
    error_message = "암호화·1 shard·24시간 버퍼를 유지합니다."
  }
  assert {
    condition     = jsondecode(aws_cloudwatch_log_destination_policy.aws.access_policy).Statement[0].Principal.AWS == ["444455556666"]
    error_message = "명시한 계정 외에는 구독할 수 없습니다."
  }
  assert {
    condition     = contains(jsondecode(aws_iam_role.delivery.assume_role_policy).Statement[0].Condition.ArnLike["aws:SourceArn"], "arn:aws:logs:ap-northeast-2:111122223333:*")
    error_message = "Destination 생성 검증을 위해 수신 계정의 SourceArn도 필요합니다."
  }
  assert {
    condition     = aws_lambda_event_source_mapping.aws.maximum_retry_attempts == 3 && aws_lambda_event_source_mapping.aws.destination_config[0].on_failure[0].destination_arn == aws_s3_bucket.failed.arn && contains(aws_lambda_event_source_mapping.aws.function_response_types, "ReportBatchItemFailures")
    error_message = "실패 원본 보관과 부분 실패 재시도를 유지합니다."
  }
  assert {
    condition     = aws_s3_bucket_public_access_block.failed.block_public_policy && !aws_s3_bucket.failed.force_destroy && jsondecode(aws_iam_role_policy.aws_read.policy).Statement[1].Condition.StringEquals["s3:ResourceAccount"] == var.aws_account_id
    error_message = "실패 로그를 공개하거나 다른 계정 버킷으로 보내면 안 됩니다."
  }
  assert {
    condition     = strcontains(google_iam_workload_identity_pool_provider.aws.attribute_condition, "assumed-role/delta-platform-logs-gcp/") && google_pubsub_subscription.logs.dead_letter_policy[0].max_delivery_attempts == 10 && google_pubsub_subscription.logs.message_retention_duration == "86400s"
    error_message = "GCP WIF는 수집기 역할로 제한하고 실패 메시지는 DLQ로 보냅니다."
  }
  assert {
    condition     = aws_lambda_function.gcp.environment[0].variables.DESTINATION_LOG_STREAM == "gcp-cloud-logging" && aws_cloudwatch_log_group.central["gcp"].retention_in_days == 3
    error_message = "중앙 로그 스트림·보관 기간을 유지합니다."
  }
  assert {
    condition     = strcontains(google_logging_project_sink.logs.filter, "gce_instance") && strcontains(google_logging_project_sink.logs.filter, "cloud_run_revision") && strcontains(google_logging_project_sink.logs.filter, "NOT logName:")
    error_message = "기본 GCP 범위는 앱·VM·네트워크이며 전체 감사 로그는 제외합니다."
  }
}
run "reject_unrestricted_source" {
  command = plan
  variables { source_aws_accounts = ["*"] }
  expect_failures = [var.source_aws_accounts]
}
run "reject_empty_sink_filter" {
  command = plan
  variables { gcp_log_filter = " " }
  expect_failures = [var.gcp_log_filter]
}
