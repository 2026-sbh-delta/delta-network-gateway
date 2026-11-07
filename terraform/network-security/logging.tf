resource "aws_cloudwatch_log_group" "firewall" {
  for_each          = toset(["FLOW", "ALERT"])
  name              = "/aws/vendedlogs/network-firewall/${local.prefix}/${lower(each.key)}"
  retention_in_days = var.log_retention_days
  skip_destroy      = var.retain_logs_on_destroy
  tags              = { Name = "${local.prefix}-${lower(each.key)}" }
  depends_on        = [terraform_data.contract]
}

locals {
  network_firewall_enterprise_log_groups = {
    FLOW  = "/delta/security/nfw-flow"
    ALERT = "/delta/security/nfw-alert"
  }
}

# Keep the legacy groups and their history while new Network Firewall events use
# enterprise names after the firewall is restored.
resource "aws_cloudwatch_log_group" "firewall_enterprise" {
  for_each          = local.network_firewall_enterprise_log_groups
  name              = each.value
  log_group_class   = "STANDARD"
  retention_in_days = var.log_retention_days
  skip_destroy      = var.retain_logs_on_destroy
  tags              = { Name = "${local.prefix}-nfw-${lower(each.key)}" }
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
      Resource = concat(
        [for group in values(aws_cloudwatch_log_group.firewall) : "${group.arn}:log-stream:*"],
        [for group in values(aws_cloudwatch_log_group.firewall_enterprise) : "${group.arn}:log-stream:*"]
      )
      Condition = {
        StringEquals = { "aws:SourceAccount" = var.central_account_id }
        ArnLike      = { "aws:SourceArn" = "arn:${data.aws_partition.current.partition}:logs:${var.region}:${var.central_account_id}:*" }
      }
    }]
  })
}

resource "aws_networkfirewall_logging_configuration" "central" {
  count        = local.firewall_enabled ? 1 : 0
  firewall_arn = aws_networkfirewall_firewall.central[0].arn
  logging_configuration {
    dynamic "log_destination_config" {
      for_each = aws_cloudwatch_log_group.firewall_enterprise
      content {
        log_type             = log_destination_config.key
        log_destination_type = "CloudWatchLogs"
        log_destination      = { logGroup = log_destination_config.value.name }
      }
    }
  }
  depends_on = [aws_cloudwatch_log_resource_policy.firewall]
}

# A cross-account logical Destination reuses the receiver account's delivery role.
resource "aws_cloudwatch_log_subscription_filter" "network_firewall_platform" {
  for_each = var.network_firewall_logs_forwarding_enabled ? aws_cloudwatch_log_group.firewall_enterprise : {}

  name            = "delta-platform-central"
  log_group_name  = each.value.name
  filter_pattern  = ""
  destination_arn = var.network_firewall_logs_destination_arn
  distribution    = "Random"
}
