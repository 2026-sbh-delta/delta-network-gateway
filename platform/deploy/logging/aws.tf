locals {
  functions = { aws = "${var.name}-aws", gcp = "${var.name}-gcp" }
  lambda_trust = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole"
  }] })
}
# 플랫폼 중앙 로그는 /delta-cp/ 아래에 둔다. 같은 계정의 A 기업 로그(/delta/…, /aws/…)와 이름으로 나눈다(11/5).
resource "aws_cloudwatch_log_group" "central" {
  for_each          = local.functions
  name              = "/delta-cp/central/${each.key}"
  retention_in_days = var.retention_days
}
resource "aws_cloudwatch_log_group" "runtime" {
  for_each          = local.functions
  name              = "/aws/lambda/${each.value}"
  retention_in_days = var.retention_days
}
resource "aws_iam_role" "collector" {
  for_each           = local.functions
  name               = each.value
  assume_role_policy = local.lambda_trust
}
resource "aws_iam_role_policy" "logs" {
  for_each = local.functions
  name     = "write-own-log-groups"
  role     = aws_iam_role.collector[each.key].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"],
    Resource = concat(
      ["${aws_cloudwatch_log_group.central[each.key].arn}:*", "${aws_cloudwatch_log_group.runtime[each.key].arn}:*"],
      each.key == "aws" && var.gcp_security_relay_enabled ? ["${aws_cloudwatch_log_group.central["gcp"].arn}:*"] : []
    )
  }] })
}
resource "aws_kinesis_stream" "aws" {
  name             = "${var.name}-aws"
  shard_count      = 1
  retention_period = 24
  encryption_type  = "KMS"
  kms_key_id       = "alias/aws/kinesis"
  stream_mode_details { stream_mode = "PROVISIONED" }
}
resource "aws_iam_role" "delivery" {
  name = "${var.name}-delivery"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Principal = { Service = "logs.amazonaws.com" }, Action = "sts:AssumeRole",
    Condition = { ArnLike = { "aws:SourceArn" = [for a in setunion(var.source_aws_accounts, toset([var.aws_account_id])) : "arn:aws:logs:${var.aws_region}:${a}:*"] } }
  }] })
}
resource "aws_iam_role_policy" "delivery" {
  name = "write-log-stream"
  role = aws_iam_role.delivery.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Action = ["kinesis:PutRecord", "kinesis:PutRecords"], Resource = aws_kinesis_stream.aws.arn
  }] })
}
resource "aws_cloudwatch_log_destination" "aws" {
  name       = var.name
  role_arn   = aws_iam_role.delivery.arn
  target_arn = aws_kinesis_stream.aws.arn
  depends_on = [aws_iam_role_policy.delivery]
}
resource "aws_cloudwatch_log_destination_policy" "aws" {
  destination_name = aws_cloudwatch_log_destination.aws.name
  access_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { AWS = sort(tolist(var.source_aws_accounts)) },
    Action = "logs:PutSubscriptionFilter", Resource = aws_cloudwatch_log_destination.aws.arn
  }] })
}
resource "aws_s3_bucket" "failed" {
  bucket        = "${var.name}-failed-${var.aws_account_id}-${var.aws_region}"
  force_destroy = false
}
resource "aws_s3_bucket_public_access_block" "failed" {
  bucket                  = aws_s3_bucket.failed.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_server_side_encryption_configuration" "failed" {
  bucket = aws_s3_bucket.failed.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}
resource "aws_s3_bucket_lifecycle_configuration" "failed" {
  bucket = aws_s3_bucket.failed.id
  rule {
    id     = "failed-records-seven-days"
    status = "Enabled"
    filter {}
    expiration { days = 7 }
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }
}
resource "aws_s3_bucket_policy" "failed" {
  bucket = aws_s3_bucket.failed.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Deny", Principal = "*", Action = "s3:*",
    Resource  = [aws_s3_bucket.failed.arn, "${aws_s3_bucket.failed.arn}/*"],
    Condition = { Bool = { "aws:SecureTransport" = "false" } }
  }] })
}
resource "aws_iam_role_policy" "aws_read" {
  role = aws_iam_role.collector["aws"].id
  name = "consume-and-retain-failures"
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["kinesis:DescribeStream", "kinesis:DescribeStreamSummary", "kinesis:GetRecords", "kinesis:GetShardIterator", "kinesis:ListShards"], Resource = aws_kinesis_stream.aws.arn },
    { Effect = "Allow", Action = ["s3:PutObject", "s3:ListBucket"], Resource = [aws_s3_bucket.failed.arn, "${aws_s3_bucket.failed.arn}/*"], Condition = { StringEquals = { "s3:ResourceAccount" = var.aws_account_id } } }
  ] })
}
resource "aws_lambda_function" "aws" {
  function_name    = local.functions.aws
  role             = aws_iam_role.collector["aws"].arn
  handler          = "aws_collector.handler"
  runtime          = "python3.13"
  architectures    = ["x86_64"]
  memory_size      = 256
  timeout          = 90
  filename         = var.aws_lambda_zip
  source_code_hash = filebase64sha256(var.aws_lambda_zip)
  environment {
    variables = merge({
      DESTINATION_LOG_GROUP   = aws_cloudwatch_log_group.central["aws"].name
      ALLOWED_SOURCE_ACCOUNTS = join(",", sort(tolist(var.source_aws_accounts)))
      SOURCE_STREAM_ARN       = aws_kinesis_stream.aws.arn
      COLLECTOR_NAME_PREFIX   = var.name
    }, var.gcp_security_relay_enabled ? { GCP_DESTINATION_LOG_GROUP = aws_cloudwatch_log_group.central["gcp"].name } : {})
  }
  depends_on = [aws_iam_role_policy.logs, aws_iam_role_policy.aws_read]
}
resource "aws_lambda_event_source_mapping" "aws" {
  event_source_arn               = aws_kinesis_stream.aws.arn
  function_name                  = aws_lambda_function.aws.arn
  starting_position              = "TRIM_HORIZON"
  batch_size                     = 1
  maximum_retry_attempts         = 3
  maximum_record_age_in_seconds  = 3600
  bisect_batch_on_function_error = true
  function_response_types        = ["ReportBatchItemFailures"]
  destination_config {
    on_failure { destination_arn = aws_s3_bucket.failed.arn }
  }
  depends_on = [aws_iam_role_policy.aws_read, aws_s3_bucket_public_access_block.failed]
}
resource "aws_cloudwatch_metric_alarm" "errors" {
  for_each            = local.functions
  alarm_name          = "${each.value}-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = { FunctionName = each.value }
}
resource "aws_cloudwatch_metric_alarm" "lag" {
  alarm_name          = "${var.name}-aws-lag"
  namespace           = "AWS/Lambda"
  metric_name         = "IteratorAge"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 300000
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = { FunctionName = local.functions.aws }
}
resource "aws_cloudwatch_log_metric_filter" "partial_failure" {
  name           = "${var.name}-aws-partial-failure"
  log_group_name = aws_cloudwatch_log_group.runtime["aws"].name
  pattern        = "{ $.type = \"ingest_summary\" && $.failed > 0 }"
  metric_transformation {
    name      = "FailedEnvelopes"
    namespace = "DeltaPlatform/Logging"
    value     = "$.failed"
  }
}
resource "aws_cloudwatch_metric_alarm" "partial_failure" {
  alarm_name          = "${var.name}-aws-partial-failure"
  namespace           = "DeltaPlatform/Logging"
  metric_name         = "FailedEnvelopes"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
}
resource "aws_cloudwatch_metric_alarm" "failure_delivery" {
  alarm_name          = "${var.name}-aws-failure-delivery"
  namespace           = "AWS/Lambda"
  metric_name         = "DestinationDeliveryFailures"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = { FunctionName = local.functions.aws }
}
