# Keep local finding storage independent of the detector, ANF and forwarding modes.
resource "aws_cloudwatch_log_group" "guardduty_findings" {
  name              = "/delta/security/guardduty"
  log_group_class   = "STANDARD"
  retention_in_days = var.log_retention_days
  skip_destroy      = var.retain_logs_on_destroy
  tags              = { Name = "${local.prefix}-guardduty-findings" }
  depends_on        = [terraform_data.contract]
}

# Logs targets use a resource policy, not an EventBridge execution role.
resource "aws_cloudwatch_log_resource_policy" "guardduty_findings" {
  policy_name = "${local.prefix}-guardduty-findings-delivery"
  policy_document = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "GuardDutyFindingsWrite"
      Effect    = "Allow"
      Principal = { Service = ["events.amazonaws.com", "delivery.logs.amazonaws.com"] }
      Action    = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource  = "${aws_cloudwatch_log_group.guardduty_findings.arn}:log-stream:*"
    }]
  })
}

resource "aws_cloudwatch_event_rule" "guardduty_findings" {
  name           = "${local.prefix}-guardduty-findings"
  description    = "Store this account's GuardDuty finding events in the enterprise log group."
  event_bus_name = "default"
  state          = "ENABLED"
  event_pattern = jsonencode({
    source        = ["aws.guardduty"]
    "detail-type" = ["GuardDuty Finding"]
    account       = [var.central_account_id]
    region        = [var.region]
  })
  depends_on = [terraform_data.contract]
}

resource "aws_cloudwatch_event_target" "guardduty_findings" {
  event_bus_name = aws_cloudwatch_event_rule.guardduty_findings.event_bus_name
  rule           = aws_cloudwatch_event_rule.guardduty_findings.name
  target_id      = "guardduty-cloudwatch-logs"
  arn            = aws_cloudwatch_log_group.guardduty_findings.arn
  # Omit input/input_path/input_transformer to preserve the entire event JSON.
  depends_on = [aws_cloudwatch_log_resource_policy.guardduty_findings]
}

# A cross-account logical Destination reuses the receiver's delivery role.
resource "aws_cloudwatch_log_subscription_filter" "guardduty_platform" {
  count = var.guardduty_findings_forwarding_enabled ? 1 : 0

  name            = "delta-platform-central"
  log_group_name  = aws_cloudwatch_log_group.guardduty_findings.name
  filter_pattern  = ""
  destination_arn = var.guardduty_findings_destination_arn
  distribution    = "Random"
}
