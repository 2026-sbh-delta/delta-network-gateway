# All runs use mock providers and only plan; they never create findings or logs.
mock_provider "aws" {
  source          = "./tests/mocks"
  override_during = plan
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:ap-northeast-2:111122223333:log-group:/delta/security/guardduty" }
  }
  mock_resource "aws_cloudwatch_event_rule" {
    defaults = { arn = "arn:aws:events:ap-northeast-2:111122223333:rule/delta-lab-guardduty-findings" }
  }
  mock_resource "aws_cloudwatch_log_subscription_filter" {
    # Optional/computed: model an absent delivery role as the provider's empty value.
    defaults = { role_arn = "" }
  }
}

variables {
  inspection_mode = "disabled"
}

override_data {
  target = data.aws_availability_zone.selected["apne2-az1"]
  values = { zone_id = "apne2-az1", name = "ap-northeast-2a", region = "ap-northeast-2", zone_type = "availability-zone" }
}

override_data {
  target = data.aws_availability_zone.selected["apne2-az2"]
  values = { zone_id = "apne2-az2", name = "ap-northeast-2b", region = "ap-northeast-2", zone_type = "availability-zone" }
}

run "default_findings_path" {
  command = plan

  assert {
    condition = jsondecode(aws_cloudwatch_event_rule.guardduty_findings.event_pattern) == {
      source        = ["aws.guardduty"]
      "detail-type" = ["GuardDuty Finding"]
      account       = ["111122223333"]
      region        = ["ap-northeast-2"]
    } && aws_cloudwatch_event_rule.guardduty_findings.event_bus_name == "default" && aws_cloudwatch_event_rule.guardduty_findings.state == "ENABLED"
    error_message = "Select every GuardDuty Finding from only the source account and Region on the default bus."
  }
  assert {
    condition = (
      aws_cloudwatch_log_group.guardduty_findings.name == "/delta/security/guardduty" &&
      aws_cloudwatch_log_group.guardduty_findings.log_group_class == "STANDARD" &&
      aws_cloudwatch_log_group.guardduty_findings.retention_in_days == 30 &&
      aws_cloudwatch_log_group.guardduty_findings.skip_destroy
    )
    error_message = "Keep enterprise finding logs separate from platform logs with finite retention and destroy preservation."
  }
  assert {
    condition = (
      length(jsondecode(aws_cloudwatch_log_resource_policy.guardduty_findings.policy_document).Statement) == 1 &&
      jsondecode(aws_cloudwatch_log_resource_policy.guardduty_findings.policy_document).Statement[0].Effect == "Allow" &&
      toset(jsondecode(aws_cloudwatch_log_resource_policy.guardduty_findings.policy_document).Statement[0].Principal.Service) == toset(["events.amazonaws.com", "delivery.logs.amazonaws.com"]) &&
      toset(jsondecode(aws_cloudwatch_log_resource_policy.guardduty_findings.policy_document).Statement[0].Action) == toset(["logs:CreateLogStream", "logs:PutLogEvents"]) &&
      jsondecode(aws_cloudwatch_log_resource_policy.guardduty_findings.policy_document).Statement[0].Resource == "${aws_cloudwatch_log_group.guardduty_findings.arn}:log-stream:*"
    )
    error_message = "Permit the two AWS delivery services to create/write streams only in the enterprise finding log group."
  }
  assert {
    condition = (
      aws_cloudwatch_event_target.guardduty_findings.arn == aws_cloudwatch_log_group.guardduty_findings.arn &&
      aws_cloudwatch_event_target.guardduty_findings.rule == aws_cloudwatch_event_rule.guardduty_findings.name &&
      aws_cloudwatch_event_target.guardduty_findings.role_arn == null &&
      aws_cloudwatch_event_target.guardduty_findings.input == null &&
      aws_cloudwatch_event_target.guardduty_findings.input_path == null &&
      length(aws_cloudwatch_event_target.guardduty_findings.input_transformer) == 0
    )
    error_message = "Use the resource-policy Logs target and preserve the complete original event JSON without an execution role."
  }
  assert {
    condition = (
      length(aws_cloudwatch_log_subscription_filter.guardduty_platform) == 1 &&
      aws_cloudwatch_log_subscription_filter.guardduty_platform[0].log_group_name == aws_cloudwatch_log_group.guardduty_findings.name &&
      aws_cloudwatch_log_subscription_filter.guardduty_platform[0].destination_arn == "arn:aws:logs:ap-northeast-2:616723161368:destination:delta-platform-logs" &&
      aws_cloudwatch_log_subscription_filter.guardduty_platform[0].filter_pattern == "" &&
      aws_cloudwatch_log_subscription_filter.guardduty_platform[0].distribution == "Random" &&
      aws_cloudwatch_log_subscription_filter.guardduty_platform[0].role_arn == "" &&
      output.guardduty_findings.forwarding_enabled && output.guardduty_findings.subscription_filter_name == "delta-platform-central"
    )
    error_message = "Forward all new events from only the finding group to the platform logical Destination without a sender role."
  }
  assert {
    condition     = output.inspection_mode == "disabled" && length(aws_networkfirewall_firewall.central) == 0 && length(output.log_groups) == 2 && output.guardduty.enabled
    error_message = "Finding forwarding must preserve existing firewall log outputs, disabled ANF mode and active GuardDuty."
  }
}

run "stop_forwarding_keeps_local_storage" {
  command = plan
  variables {
    guardduty_findings_forwarding_enabled = false
  }
  assert {
    condition = (
      length(aws_cloudwatch_log_subscription_filter.guardduty_platform) == 0 &&
      !output.guardduty_findings.forwarding_enabled && output.guardduty_findings.subscription_filter_name == null &&
      aws_cloudwatch_log_group.guardduty_findings.name == "/delta/security/guardduty" &&
      aws_cloudwatch_event_rule.guardduty_findings.state == "ENABLED" &&
      aws_cloudwatch_event_target.guardduty_findings.arn == aws_cloudwatch_log_group.guardduty_findings.arn &&
      length(jsondecode(aws_cloudwatch_log_resource_policy.guardduty_findings.policy_document).Statement) == 1 && output.guardduty.enabled
    )
    error_message = "Disabling forwarding removes only the subscription and keeps local logging and detection configured."
  }
}

run "suspended_detector_keeps_findings_path" {
  command = plan
  variables {
    guardduty_enabled = false
  }
  assert {
    condition = (
      !output.guardduty.enabled && length(aws_guardduty_detector_feature.protection) == 7 &&
      aws_cloudwatch_log_group.guardduty_findings.skip_destroy &&
      aws_cloudwatch_event_rule.guardduty_findings.state == "ENABLED" &&
      length(aws_cloudwatch_log_subscription_filter.guardduty_platform) == 1
    )
    error_message = "Suspending detection must retain the finding log path and all managed feature resources."
  }
}

run "use_existing_retention_controls" {
  command = plan
  variables {
    log_retention_days     = 7
    retain_logs_on_destroy = false
  }
  assert {
    condition     = aws_cloudwatch_log_group.guardduty_findings.retention_in_days == 7 && !aws_cloudwatch_log_group.guardduty_findings.skip_destroy
    error_message = "Apply the existing shared retention and destroy-preservation inputs to the new source group."
  }
}

run "reject_destination_in_another_region" {
  command = plan
  variables {
    guardduty_findings_destination_arn = "arn:aws:logs:ap-northeast-1:616723161368:destination:delta-platform-logs"
  }
  expect_failures = [var.guardduty_findings_destination_arn]
}

run "reject_destination_in_another_account" {
  command = plan
  variables {
    guardduty_findings_destination_arn = "arn:aws:logs:ap-northeast-2:777788889999:destination:delta-platform-logs"
  }
  expect_failures = [var.guardduty_findings_destination_arn]
}

run "reject_direct_stream_destination" {
  command = plan
  variables {
    guardduty_findings_destination_arn = "arn:aws:kinesis:ap-northeast-2:616723161368:stream/delta-platform-logs-aws"
  }
  expect_failures = [var.guardduty_findings_destination_arn]
}

run "reject_empty_destination" {
  command = plan
  variables {
    guardduty_findings_destination_arn = ""
  }
  expect_failures = [var.guardduty_findings_destination_arn]
}
