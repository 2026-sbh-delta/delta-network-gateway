mock_provider "google" {}

variables {
  project_id = "delta-example-01"
}

run "defaults_build_a_scanner_only_staging" {
  command = plan

  assert {
    condition     = google_cloud_run_v2_service.app.ingress == "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
    error_message = "Only the load balancer may reach Cloud Run."
  }
  assert {
    condition     = one([for r in google_compute_security_policy.scanner.rule : r.action if r.priority == 2147483647]) == "deny(403)"
    error_message = "The default Cloud Armor rule must deny."
  }
  assert {
    condition     = length(google_compute_security_policy_rule.fixed_scanners) == 0
    error_message = "No fixed scanner rule without scanner_public_cidrs."
  }
  assert {
    condition     = output.tool_config.cloud == "gcp" && output.tool_config.dynamic_scanner_access
    error_message = "tool_config must describe a gcp target with dynamic scanner access."
  }
  assert {
    condition     = length(output.tool_config.allowed_image_prefixes) == 1 && output.tool_config.allowed_image_prefixes[0] == "asia-northeast3-docker.pkg.dev/delta-example-01/delta-lab-staging/"
    error_message = "The tool must accept only this project's staging repository by default."
  }
}

run "fixed_scanner_cidrs_get_their_own_rule" {
  command = plan
  variables {
    dynamic_scanner_access = false
    scanner_public_cidrs   = ["203.0.113.0/24"]
  }

  assert {
    condition     = google_compute_security_policy_rule.fixed_scanners[0].priority == 1001
    error_message = "Fixed CIDRs sit behind the tool-managed rule."
  }
}

run "nothing_can_reach_without_scanner_sources" {
  command = plan
  variables {
    dynamic_scanner_access = false
  }
  expect_failures = [terraform_data.contract]
}

run "open_cidrs_are_rejected" {
  command = plan
  variables {
    scanner_public_cidrs = ["0.0.0.0/0"]
  }
  expect_failures = [var.scanner_public_cidrs]
}
