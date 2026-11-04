mock_provider "google" {}
mock_provider "google-beta" {}
mock_provider "aws" {}
variables {
  project_id     = "test-project"
  aws_account_id = "111122223333"
}
run "isolated_and_keyless" {
  command = plan
  assert {
    condition     = google_compute_network.sandbox.delete_default_routes_on_create && !google_compute_subnetwork.sandbox.private_ip_google_access
    error_message = "Sandbox must not inherit internet or Google private access routes."
  }
  assert {
    condition     = google_compute_firewall.deny_egress.direction == "EGRESS" && google_compute_firewall.deny_egress.destination_ranges == toset(["0.0.0.0/0"])
    error_message = "Sandbox outbound access must be denied."
  }
  assert {
    condition     = !strcontains(jsonencode(google_project_iam_custom_role.runner.permissions), "run.services.update")
    error_message = "Runner creates owned disposable services; it must not update existing deployments."
  }
  assert {
    condition     = strcontains(google_iam_workload_identity_pool_provider.aws.attribute_condition, "delta-platform-app") && strcontains(google_iam_workload_identity_pool_provider.aws.attribute_condition, "delta-lab-tooling-host")
    error_message = "Federation must be limited to the two approved AWS roles."
  }
  assert {
    condition     = google_artifact_registry_repository.sandbox.cleanup_policy_dry_run == false
    error_message = "Expired test images should be cleaned."
  }
}
