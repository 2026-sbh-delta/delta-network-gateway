resource "aws_cloudwatch_log_group" "firewall" {
  for_each          = toset(["FLOW", "ALERT"])
  name              = "/aws/vendedlogs/network-firewall/${local.prefix}/${lower(each.key)}"
  retention_in_days = var.log_retention_days
  skip_destroy      = var.retain_logs_on_destroy
  tags              = { Name = "${local.prefix}-${lower(each.key)}" }
  depends_on        = [terraform_data.contract]
}

resource "aws_cloudwatch_log_resource_policy" "firewall" {
  policy_name = "${local.prefix}-firewall-delivery"
  policy_document = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AWSLogDeliveryWrite"
      Effect    = "Allow"
      Principal = { Service = "delivery.logs.amazonaws.com" }
      Action    = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource  = [for group in values(aws_cloudwatch_log_group.firewall) : "${group.arn}:log-stream:*"]
      Condition = {
        StringEquals = { "aws:SourceAccount" = var.central_account_id }
        ArnLike      = { "aws:SourceArn" = "arn:${data.aws_partition.current.partition}:logs:${var.region}:${var.central_account_id}:*" }
      }
    }]
  })
}

resource "aws_networkfirewall_logging_configuration" "central" {
  firewall_arn = aws_networkfirewall_firewall.central.arn
  logging_configuration {
    dynamic "log_destination_config" {
      for_each = aws_cloudwatch_log_group.firewall
      content {
        log_type             = log_destination_config.key
        log_destination_type = "CloudWatchLogs"
        log_destination      = { logGroup = log_destination_config.value.name }
      }
    }
  }
  depends_on = [aws_cloudwatch_log_resource_policy.firewall]
}
