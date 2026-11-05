mock_provider "google" {
  override_during = plan
  mock_data "google_project" { defaults = { number = "123456789012", project_id = "term2-delta" } }
  mock_resource "google_compute_network" {
    defaults = { id = "projects/term2-delta/global/networks/mock", self_link = "https://www.googleapis.com/compute/v1/projects/term2-delta/global/networks/mock" }
  }
  mock_resource "google_compute_subnetwork" {
    defaults = { id = "projects/term2-delta/regions/asia-northeast3/subnetworks/mock", self_link = "https://www.googleapis.com/compute/v1/projects/term2-delta/regions/asia-northeast3/subnetworks/mock" }
  }
  mock_resource "google_compute_instance" {
    defaults = { self_link = "https://www.googleapis.com/compute/v1/projects/term2-delta/zones/asia-northeast3-a/instances/mock" }
  }
  mock_resource "google_compute_instance_group" {
    defaults = { self_link = "https://www.googleapis.com/compute/v1/projects/term2-delta/zones/asia-northeast3-a/instanceGroups/mock" }
  }
  mock_resource "google_compute_network_firewall_policy" { defaults = { name = "delta-lab-central" } }
  mock_resource "google_compute_ha_vpn_gateway" { defaults = { id = "projects/term2-delta/regions/asia-northeast3/vpnGateways/delta-lab-aws" } }
  mock_resource "google_compute_router" { defaults = { id = "projects/term2-delta/regions/asia-northeast3/routers/delta-lab-aws" } }
  mock_resource "google_compute_external_vpn_gateway" { defaults = { id = "projects/term2-delta/global/externalVpnGateways/delta-lab-aws-peer" } }
  mock_resource "google_compute_vpn_tunnel" { defaults = { self_link = "https://www.googleapis.com/compute/v1/projects/term2-delta/regions/asia-northeast3/vpnTunnels/mock" } }
  mock_resource "google_network_security_security_profile" { defaults = { id = "projects/term2-delta/locations/global/securityProfiles/delta-lab-threat" } }
  mock_resource "google_network_security_security_profile_group" { defaults = { id = "projects/term2-delta/locations/global/securityProfileGroups/delta-lab-threat" } }
  mock_resource "google_network_security_firewall_endpoint" { defaults = { id = "projects/term2-delta/locations/asia-northeast3-a/firewallEndpoints/delta-lab-central" } }
}
mock_provider "google-beta" {
  override_during = plan
  mock_resource "google_project_service_identity" { defaults = { email = "service-123456789012@gcp-sa-pubsub.iam.gserviceaccount.com" } }
}

run "bootstrap_central_and_workload_are_separate" {
  command = plan
  assert {
    condition     = length(google_compute_network.vpc) == 2 && google_compute_network.vpc["central"].name != google_compute_network.vpc["workload"].name && google_compute_ha_vpn_gateway.aws.network == google_compute_network.vpc["central"].id
    error_message = "The central detection/connectivity VPC must be separate from the workload VPC."
  }
  assert {
    condition     = google_network_security_firewall_endpoint.central[0].parent == "projects/term2-delta" && google_network_security_security_profile.threat.parent == "projects/term2-delta" && length(google_network_security_firewall_endpoint_association.vpc) == 2 && google_network_security_firewall_endpoint_association.vpc["workload"].location == google_compute_instance.server.zone
    error_message = "Share a project-level endpoint across VPCs in the workload zone, without requiring an organization."
  }
  assert {
    condition     = length(google_compute_instance.server.network_interface[0].access_config) == 0 && google_compute_instance.server.network_interface[0].network_ip == "10.30.1.10" && length(google_compute_vpn_tunnel.aws) == 0 && length(google_network_connectivity_spoke.aws) == 0
    error_message = "Bootstrap must have a private server and no incomplete AWS tunnels."
  }
  assert {
    condition     = google_compute_network_firewall_policy_rule.ingress["internet_inspect"].action == "apply_security_profile_group" && google_compute_network_firewall_policy_rule.ingress["internet_deny"].action == "deny" && toset(google_compute_forwarding_rule.demo.ports) == toset(["8080", "8081"])
    error_message = "Forward both control ports and use real GCP firewall inspect/deny actions."
  }
}
run "wrong_project_number" {
  command = plan
  variables { project_number = "999999999999" }
  expect_failures = [terraform_data.contract]
}
run "wrong_project" {
  command = plan
  variables { project_id = "other-project" }
  expect_failures = [var.project_id]
}
run "wrong_zone" {
  command = plan
  variables { zone = "us-central1-a" }
  expect_failures = [terraform_data.contract]
}
run "overlapping_central_workload" {
  command = plan
  variables { subnets = { central = "10.30.1.0/24", workload = "10.30.1.0/24" } }
  expect_failures = [terraform_data.contract]
}
run "overlapping_aws" {
  command = plan
  variables { aws_cidrs = ["10.30.0.0/16"] }
  expect_failures = [terraform_data.contract]
}
run "no_internet_default_route_advertisement" {
  command = plan
  variables { aws_cidrs = ["0.0.0.0/0"] }
  expect_failures = [terraform_data.contract]
}
run "no_unrestricted_demo_source" {
  command = plan
  variables { internet_source_cidrs = ["0.0.0.0/0"] }
  expect_failures = [var.internet_source_cidrs]
}
run "no_floating_image" {
  command = plan
  variables { server_image = "projects/ubuntu-os-cloud/global/images/family/ubuntu-2404-lts-amd64" }
  expect_failures = [var.server_image]
}

run "complete_four_tunnel_handoff" {
  command = plan
  variables {
    aws_vpn_tunnels = {
      a1 = { gcp_interface = 0, aws_interface = 0, outside_ip = "1.1.1.1", inside_cidr = "169.254.10.0/30", aws_inside_ip = "169.254.10.1", gcp_inside_ip = "169.254.10.2", aws_asn = 64512, secret_version = 1 }
      a2 = { gcp_interface = 0, aws_interface = 1, outside_ip = "8.8.8.8", inside_cidr = "169.254.10.4/30", aws_inside_ip = "169.254.10.5", gcp_inside_ip = "169.254.10.6", aws_asn = 64512, secret_version = 1 }
      b1 = { gcp_interface = 1, aws_interface = 2, outside_ip = "9.9.9.9", inside_cidr = "169.254.10.8/30", aws_inside_ip = "169.254.10.9", gcp_inside_ip = "169.254.10.10", aws_asn = 64512, secret_version = 1 }
      b2 = { gcp_interface = 1, aws_interface = 3, outside_ip = "4.4.4.4", inside_cidr = "169.254.10.12/30", aws_inside_ip = "169.254.10.13", gcp_inside_ip = "169.254.10.14", aws_asn = 64512, secret_version = 1 }
    }
    vpn_shared_secrets = { a1 = "synthetic-test-key-a1", a2 = "synthetic-test-key-a2", b1 = "synthetic-test-key-b1", b2 = "synthetic-test-key-b2" }
  }
  assert {
    condition     = length(google_compute_vpn_tunnel.aws) == 4 && length(google_compute_router_peer.aws) == 4 && length(google_network_connectivity_spoke.aws) == 1 && toset(google_network_connectivity_spoke.aws[0].linked_vpn_tunnels[0].include_export_ranges) == toset(["10.10.0.0/16"])
    error_message = "The complete handoff must create four BGP tunnels and filter imported AWS prefixes."
  }
  assert {
    condition     = alltrue([for peer in google_compute_router_peer.aws : one(peer.advertised_ip_ranges).range == "10.30.1.0/24"])
    error_message = "Only the workload prefix may be advertised to AWS."
  }
}
run "reject_partial_handoff" {
  command = plan
  variables {
    aws_vpn_tunnels    = { a1 = { gcp_interface = 0, aws_interface = 0, outside_ip = "1.1.1.1", inside_cidr = "169.254.10.0/30", aws_inside_ip = "169.254.10.1", gcp_inside_ip = "169.254.10.2", aws_asn = 64512, secret_version = 1 } }
    vpn_shared_secrets = { a1 = "synthetic-test-key-a1" }
  }
  expect_failures = [terraform_data.contract]
}

run "bypass_preserves_endpoint_and_basic_denies" {
  command = plan
  variables { inspection_mode = "bypass" }
  assert {
    condition     = length(google_network_security_firewall_endpoint.central) == 1 && length(google_network_security_firewall_endpoint_association.vpc) == 2 && google_compute_network_firewall_policy_rule.ingress["private_inspect"].action == "allow" && google_compute_network_firewall_policy_rule.ingress["internet_inspect"].action == "allow" && google_compute_network_firewall_policy_rule.private_egress.action == "allow"
    error_message = "Bypass must keep endpoints while removing all three L7 interception actions."
  }
  assert {
    condition     = google_compute_network_firewall_policy_rule.ingress["internet_deny"].action == "deny" && google_compute_network_firewall_policy_rule.deny_ingress.action == "deny" && google_compute_network_firewall_policy_rule.private_egress.security_profile_group == null && google_compute_network_firewall_policy_rule.private_egress.enable_logging
    error_message = "Keep basic denies/logging and remove the profile reference from an allow rule."
  }
}

run "disabled_preserves_four_tunnels_and_workload" {
  command = plan
  variables {
    inspection_mode = "disabled"
    aws_vpn_tunnels = {
      a1 = { gcp_interface = 0, aws_interface = 0, outside_ip = "1.1.1.1", inside_cidr = "169.254.10.0/30", aws_inside_ip = "169.254.10.1", gcp_inside_ip = "169.254.10.2", aws_asn = 64512, secret_version = 1 }
      a2 = { gcp_interface = 0, aws_interface = 1, outside_ip = "8.8.8.8", inside_cidr = "169.254.10.4/30", aws_inside_ip = "169.254.10.5", gcp_inside_ip = "169.254.10.6", aws_asn = 64512, secret_version = 1 }
      b1 = { gcp_interface = 1, aws_interface = 2, outside_ip = "9.9.9.9", inside_cidr = "169.254.10.8/30", aws_inside_ip = "169.254.10.9", gcp_inside_ip = "169.254.10.10", aws_asn = 64512, secret_version = 1 }
      b2 = { gcp_interface = 1, aws_interface = 3, outside_ip = "4.4.4.4", inside_cidr = "169.254.10.12/30", aws_inside_ip = "169.254.10.13", gcp_inside_ip = "169.254.10.14", aws_asn = 64512, secret_version = 1 }
    }
    vpn_shared_secrets = { a1 = "synthetic-test-key-a1", a2 = "synthetic-test-key-a2", b1 = "synthetic-test-key-b1", b2 = "synthetic-test-key-b2" }
  }
  assert {
    condition     = length(google_network_security_firewall_endpoint.central) == 0 && length(google_network_security_firewall_endpoint_association.vpc) == 0 && output.firewall_endpoint == null && output.inspection_mode == "disabled"
    error_message = "Disabled must remove billable endpoints and associations, with an explicit disabled output."
  }
  assert {
    condition     = length(google_compute_vpn_tunnel.aws) == 4 && length(google_compute_router_peer.aws) == 4 && length(google_network_connectivity_spoke.aws) == 1 && length(google_compute_network.vpc) == 2 && google_compute_instance.server.network_interface[0].network_ip == "10.30.1.10" && google_compute_route.lb_return.dest_range == "0.0.0.0/0"
    error_message = "Disabled must retain private routing, the workload and the LB return path."
  }
  assert {
    condition     = google_compute_network_firewall_policy_rule.ingress["private_inspect"].action == "allow" && google_compute_network_firewall_policy_rule.ingress["internet_inspect"].action == "allow" && google_compute_network_firewall_policy_rule.private_egress.action == "allow" && google_compute_network_firewall_policy_rule.ingress["internet_deny"].action == "deny"
    error_message = "Routing must work without L7 endpoints while the internet deny remains enforced."
  }
}

run "reject_unknown_inspection_mode" {
  command = plan
  variables { inspection_mode = "off" }
  expect_failures = [var.inspection_mode]
}
