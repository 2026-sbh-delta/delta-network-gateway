# All runs use mock providers and command=plan. They never create logs or subscriptions.
mock_provider "aws" {
  source          = "./tests/mocks"
  override_during = plan
  mock_resource "aws_cloudwatch_log_subscription_filter" {
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

run "default_network_firewall_log_forwarding" {
  command = plan

  assert {
    condition = (
      length(aws_cloudwatch_log_group.firewall_enterprise) == 2 &&
      aws_cloudwatch_log_group.firewall_enterprise["FLOW"].name == "/delta/security/nfw-flow" &&
      aws_cloudwatch_log_group.firewall_enterprise["ALERT"].name == "/delta/security/nfw-alert" &&
      alltrue([for group in values(aws_cloudwatch_log_group.firewall_enterprise) :
        group.log_group_class == "STANDARD" && group.retention_in_days == 30 && group.skip_destroy
      ])
    )
    error_message = "Create separate Standard enterprise FLOW and ALERT groups with finite retention and destroy preservation."
  }

  assert {
    condition = (
      length(aws_cloudwatch_log_subscription_filter.network_firewall_platform) == 2 &&
      alltrue([for type, subscription in aws_cloudwatch_log_subscription_filter.network_firewall_platform :
        subscription.name == "delta-platform-central" &&
        subscription.log_group_name == aws_cloudwatch_log_group.firewall_enterprise[type].name &&
        subscription.destination_arn == "arn:aws:logs:ap-northeast-2:616723161368:destination:delta-platform-logs" &&
        subscription.filter_pattern == "" &&
        subscription.distribution == "Random" &&
        subscription.role_arn == ""
      ])
    )
    error_message = "Forward every new enterprise FLOW and ALERT event to the logical Destination without a sender role."
  }

  assert {
    condition = (
      output.network_firewall_logs.log_groups == {
        FLOW  = "/delta/security/nfw-flow"
        ALERT = "/delta/security/nfw-alert"
      } &&
      output.network_firewall_logs.forwarding_enabled &&
      output.network_firewall_logs.subscription_filter_names == {
        FLOW  = "delta-platform-central"
        ALERT = "delta-platform-central"
      }
    )
    error_message = "Expose both enterprise groups and forwarding state without log contents."
  }

  assert {
    condition = (
      length(aws_cloudwatch_log_group.firewall) == 2 &&
      aws_cloudwatch_log_group.firewall["FLOW"].name == "/aws/vendedlogs/network-firewall/delta-lab/flow" &&
      aws_cloudwatch_log_group.firewall["ALERT"].name == "/aws/vendedlogs/network-firewall/delta-lab/alert" &&
      output.inspection_mode == "disabled" &&
      length(aws_networkfirewall_firewall.central) == 0 &&
      length(aws_networkfirewall_logging_configuration.central) == 0
    )
    error_message = "Retain both legacy groups and keep the firewall absent in disabled mode."
  }

  assert {
    condition = (
      length(jsondecode(aws_cloudwatch_log_resource_policy.firewall.policy_document).Statement[0].Resource) == 4 &&
      alltrue([for group in concat(
        values(aws_cloudwatch_log_group.firewall),
        values(aws_cloudwatch_log_group.firewall_enterprise)
        ) : contains(
        jsondecode(aws_cloudwatch_log_resource_policy.firewall.policy_document).Statement[0].Resource,
        "${group.arn}:log-stream:*"
      )])
    )
    error_message = "Permit Network Firewall delivery to both retained legacy and new enterprise groups."
  }
}

run "enabled_network_firewall_uses_enterprise_groups" {
  command = plan
  variables {
    inspection_mode = "enabled"
  }

  assert {
    condition = (
      length(aws_networkfirewall_logging_configuration.central) == 1 &&
      toset([for destination in one(aws_networkfirewall_logging_configuration.central[0].logging_configuration).log_destination_config :
        destination.log_type
      ]) == toset(["FLOW", "ALERT"]) &&
      alltrue([for destination in one(aws_networkfirewall_logging_configuration.central[0].logging_configuration).log_destination_config :
        destination.log_destination_type == "CloudWatchLogs" &&
        destination.log_destination.logGroup == aws_cloudwatch_log_group.firewall_enterprise[destination.log_type].name
      ])
    )
    error_message = "A restored Network Firewall must write FLOW and ALERT events only to the enterprise groups."
  }
}

run "forwarding_disabled_keeps_local_groups" {
  command = plan
  variables {
    network_firewall_logs_forwarding_enabled = false
  }

  assert {
    condition = (
      length(aws_cloudwatch_log_group.firewall_enterprise) == 2 &&
      length(aws_cloudwatch_log_subscription_filter.network_firewall_platform) == 0 &&
      !output.network_firewall_logs.forwarding_enabled &&
      alltrue([for name in values(output.network_firewall_logs.subscription_filter_names) : name == null])
    )
    error_message = "Disabling platform forwarding must retain local enterprise groups and remove only their subscriptions."
  }
}

run "custom_log_retention_reuses_shared_controls" {
  command = plan
  variables {
    log_retention_days     = 7
    retain_logs_on_destroy = false
  }

  assert {
    condition = alltrue([for group in values(aws_cloudwatch_log_group.firewall_enterprise) :
      group.retention_in_days == 7 && !group.skip_destroy
    ])
    error_message = "Enterprise groups must reuse the module's retention and destroy controls."
  }
}

run "reject_network_firewall_destination_region" {
  command = plan
  variables {
    network_firewall_logs_destination_arn = "arn:aws:logs:us-east-1:616723161368:destination:delta-platform-logs"
  }
  expect_failures = [var.network_firewall_logs_destination_arn]
}

run "reject_network_firewall_destination_account" {
  command = plan
  variables {
    network_firewall_logs_destination_arn = "arn:aws:logs:ap-northeast-2:111122223333:destination:delta-platform-logs"
  }
  expect_failures = [var.network_firewall_logs_destination_arn]
}

run "reject_network_firewall_destination_resource_type" {
  command = plan
  variables {
    network_firewall_logs_destination_arn = "arn:aws:logs:ap-northeast-2:616723161368:log-group:/delta/security/nfw-flow"
  }
  expect_failures = [var.network_firewall_logs_destination_arn]
}

run "reject_empty_network_firewall_destination" {
  command = plan
  variables {
    network_firewall_logs_destination_arn = ""
  }
  expect_failures = [var.network_firewall_logs_destination_arn]
}
