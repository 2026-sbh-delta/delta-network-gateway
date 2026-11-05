# All providers and IDs are synthetic; every run only plans.
mock_provider "aws" {
  override_during = plan
  mock_data "aws_caller_identity" {
    defaults = { account_id = "444455556666" }
  }
  mock_resource "aws_guardduty_detector" {
    defaults = {
      id  = "00000000000000000000000000000001"
      arn = "arn:aws:guardduty:ap-northeast-2:444455556666:detector/00000000000000000000000000000001"
    }
  }
  mock_resource "aws_guardduty_detector_feature" {
    defaults = { additional_configuration = [] }
  }
}

override_data {
  target = data.aws_availability_zone.selected["apne2-az1"]
  values = { zone_id = "apne2-az1", name = "ap-northeast-2a", region = "ap-northeast-2", zone_type = "availability-zone" }
}

override_data {
  target = data.aws_availability_zone.selected["apne2-az2"]
  values = { zone_id = "apne2-az2", name = "ap-northeast-2b", region = "ap-northeast-2", zone_type = "availability-zone" }
}

run "default_guardduty_policy" {
  command = plan
  assert {
    condition     = output.guardduty.enabled && output.guardduty.account_id == "444455556666" && output.guardduty.region == "ap-northeast-2" && output.guardduty.finding_publishing_frequency == "SIX_HOURS"
    error_message = "Enable account/Region detection with the explicit existing six-hour update frequency."
  }
  assert {
    condition = length(output.guardduty.features) == 7 && alltrue([
      for name in ["S3_DATA_EVENTS", "EKS_AUDIT_LOGS", "EBS_MALWARE_PROTECTION", "RDS_LOGIN_EVENTS", "LAMBDA_NETWORK_LOGS"] :
      output.guardduty.features[name].status == "ENABLED"
    ]) && output.guardduty.features["RDS_LOGIN_EVENTS"].additional_configuration["RDS_DATA_RISK"] == "ENABLED"
    error_message = "Keep every previously enabled workload protection and RDS data-risk setting."
  }
  assert {
    condition     = output.guardduty.features["AI_PROTECTION"].status == "DISABLED" && output.guardduty.features["RUNTIME_MONITORING"].status == "DISABLED" && alltrue([for status in values(output.guardduty.features["RUNTIME_MONITORING"].additional_configuration) : status == "DISABLED"])
    error_message = "Do not enable AI Protection, Runtime Monitoring or automatic security-agent management."
  }
}

run "suspend_retains_detector_and_features" {
  command = plan
  variables {
    guardduty_enabled = false
  }
  assert {
    condition     = !aws_guardduty_detector.account.enable && length(aws_guardduty_detector_feature.protection) == 7 && output.guardduty.detector_id == "00000000000000000000000000000001"
    error_message = "Suspension must retain the same detector and all feature resources instead of deleting them."
  }
}

run "reject_missing_protection" {
  command = plan
  variables {
    guardduty_features = { for name, feature in run.default_guardduty_policy.guardduty.features : name => feature if name != "S3_DATA_EVENTS" }
  }
  expect_failures = [var.guardduty_features]
}

run "reject_legacy_runtime_duplicate" {
  command = plan
  variables {
    guardduty_features = merge(run.default_guardduty_policy.guardduty.features, { EKS_RUNTIME_MONITORING = { status = "DISABLED" } })
  }
  expect_failures = [var.guardduty_features]
}

run "reject_invalid_feature_status" {
  command = plan
  variables {
    guardduty_features = merge(run.default_guardduty_policy.guardduty.features, { S3_DATA_EVENTS = { status = "OFF" } })
  }
  expect_failures = [var.guardduty_features]
}

run "reject_wrong_additional_configuration" {
  command = plan
  variables {
    guardduty_features = merge(run.default_guardduty_policy.guardduty.features, {
      RDS_LOGIN_EVENTS = { status = "DISABLED", additional_configuration = { EC2_AGENT_MANAGEMENT = "DISABLED" } }
    })
  }
  expect_failures = [var.guardduty_features]
}

run "reject_missing_agent_management" {
  command = plan
  variables {
    guardduty_features = merge(run.default_guardduty_policy.guardduty.features, {
      RUNTIME_MONITORING = { status = "DISABLED", additional_configuration = { EC2_AGENT_MANAGEMENT = "DISABLED" } }
    })
  }
  expect_failures = [var.guardduty_features]
}

run "reject_agent_on_disabled_runtime" {
  command = plan
  variables {
    guardduty_features = merge(run.default_guardduty_policy.guardduty.features, {
      RUNTIME_MONITORING = {
        status = "DISABLED"
        additional_configuration = {
          EKS_ADDON_MANAGEMENT         = "DISABLED"
          ECS_FARGATE_AGENT_MANAGEMENT = "DISABLED"
          EC2_AGENT_MANAGEMENT         = "ENABLED"
        }
      }
    })
  }
  expect_failures = [var.guardduty_features]
}

run "reject_data_risk_on_disabled_rds" {
  command = plan
  variables {
    guardduty_features = merge(run.default_guardduty_policy.guardduty.features, {
      RDS_LOGIN_EVENTS = { status = "DISABLED", additional_configuration = { RDS_DATA_RISK = "ENABLED" } }
    })
  }
  expect_failures = [var.guardduty_features]
}

run "reject_invalid_finding_frequency" {
  command = plan
  variables {
    guardduty_finding_publishing_frequency = "DAILY"
  }
  expect_failures = [var.guardduty_finding_publishing_frequency]
}

run "explicit_supported_ai_analyst_off" {
  command = plan
  variables {
    guardduty_features = merge(run.default_guardduty_policy.guardduty.features, { AI_ANALYST = { status = "DISABLED" } })
  }
  assert {
    condition     = aws_guardduty_detector_feature.protection["AI_ANALYST"].status == "DISABLED" && length(aws_guardduty_detector_feature.protection) == 8
    error_message = "If the Region reports AI_ANALYST, its disabled state must be explicitly manageable."
  }
}
