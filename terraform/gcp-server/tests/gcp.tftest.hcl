# No cloud writes. Every run uses a mock provider and command=plan.
mock_provider "google" {
  override_during = plan
  mock_data "google_project" {
    defaults = { project_id = "delta-example-project", number = "123456789012" }
  }
  mock_data "google_compute_image" {
    defaults = { family = "ubuntu-2404-lts-amd64", status = "READY", disk_size_gb = 10, self_link = "projects/ubuntu-os-cloud/global/images/ubuntu-2404-noble-amd64-v20261001" }
  }
}
override_resource {
  target          = google_compute_ha_vpn_gateway.aws
  override_during = plan
  values          = { vpn_interfaces = { ip_address = "34.1.1.1" } }
}

variables {
  vpn_tunnels = {
    aws-if0-t1 = { gcp_interface = 0, aws_interface = 0, aws_outside_ip = "3.1.1.1", inside_cidr = "169.254.10.0/30", aws_inside_ip = "169.254.10.1", gcp_inside_ip = "169.254.10.2", aws_bgp_asn = 64512 }
    aws-if0-t2 = { gcp_interface = 0, aws_interface = 1, aws_outside_ip = "3.1.1.2", inside_cidr = "169.254.10.4/30", aws_inside_ip = "169.254.10.5", gcp_inside_ip = "169.254.10.6", aws_bgp_asn = 64512 }
    aws-if1-t1 = { gcp_interface = 1, aws_interface = 2, aws_outside_ip = "3.1.1.3", inside_cidr = "169.254.10.8/30", aws_inside_ip = "169.254.10.9", gcp_inside_ip = "169.254.10.10", aws_bgp_asn = 64512 }
    aws-if1-t2 = { gcp_interface = 1, aws_interface = 3, aws_outside_ip = "3.1.1.4", inside_cidr = "169.254.10.12/30", aws_inside_ip = "169.254.10.13", gcp_inside_ip = "169.254.10.14", aws_bgp_asn = 64512 }
  }
  tunnel_shared_secrets = {
    aws-if0-t1 = "MockEphemeralSecretNotReal1"
    aws-if0-t2 = "MockEphemeralSecretNotReal2"
    aws-if1-t1 = "MockEphemeralSecretNotReal3"
    aws-if1-t2 = "MockEphemeralSecretNotReal4"
  }
}

run "private_foundation" {
  command = plan
  variables {
    vpn_tunnels           = {}
    tunnel_shared_secrets = {}
  }
  assert {
    condition     = !google_compute_network.server.auto_create_subnetworks && google_compute_network.server.delete_default_routes_on_create && !google_compute_network.server.enable_ula_internal_ipv6 && google_compute_subnetwork.server.stack_type == "IPV4_ONLY"
    error_message = "Require explicit IPv4 subnets and no internet default route."
  }
  assert {
    condition     = length(one(google_compute_instance.server.network_interface).access_config) == 0 && length(one(google_compute_instance.server.network_interface).ipv6_access_config) == 0 && length(google_compute_instance.server.service_account) == 0 && !google_compute_instance.server.can_ip_forward && google_compute_address.server.address_type == "INTERNAL"
    error_message = "The VM must have no external IP, forwarding or service-account credentials."
  }
  assert {
    condition     = google_compute_instance.server.metadata["enable-oslogin"] == "TRUE" && google_compute_instance.server.metadata["block-project-ssh-keys"] == "TRUE" && one(google_compute_instance.server.shielded_instance_config).enable_secure_boot && google_compute_instance.server.deletion_protection
    error_message = "Require OS Login, blocked project keys, Shielded VM and deletion protection."
  }
  assert {
    condition     = google_compute_firewall.iap_ssh.source_ranges == toset(["35.235.240.0/20"]) && one(google_compute_firewall.iap_ssh.allow).ports == tolist(["22"]) && google_compute_firewall.aws_test.source_ranges == var.aws_client_cidrs && one(google_compute_firewall.aws_test.allow).ports == tolist(["8080", "8081"])
    error_message = "Only IAP may SSH; both service controls accept only the AWS test source."
  }
  assert {
    condition     = length(google_compute_vpn_tunnel.aws) == 0 && length(google_compute_router_peer.aws) == 0 && length(google_compute_external_vpn_gateway.aws) == 0 && length(google_project_service.required) == 3 && alltrue([for service in google_project_service.required : !service.disable_on_destroy])
    error_message = "Foundation must work without AWS tunnels and preserve APIs on teardown."
  }
  assert {
    condition     = toset(keys(output.gateway_ips_by_interface)) == toset(["0", "1"]) && output.aws_vpn_handoff.peers.gcp_if1.gcp_interface == 1
    error_message = "Gateway handoff must use explicit interface IDs."
  }
}

run "four_tunnel_mapping" {
  command = plan
  assert {
    condition     = length(google_compute_vpn_tunnel.aws) == 4 && length(google_compute_router_interface.aws) == 4 && length(google_compute_router_peer.aws) == 4 && one(google_compute_external_vpn_gateway.aws).redundancy_type == "FOUR_IPS_REDUNDANCY" && length(one(google_compute_external_vpn_gateway.aws).interface) == 4
    error_message = "Require four distinct tunnels, interfaces and BGP peers."
  }
  assert {
    condition     = alltrue([for key, tunnel in google_compute_vpn_tunnel.aws : tunnel.vpn_gateway_interface == floor(var.vpn_tunnels[key].aws_interface / 2) && tunnel.peer_external_gateway_interface == var.vpn_tunnels[key].aws_interface && tunnel.ike_version == 2 && tunnel.shared_secret_wo_version == "1"])
    error_message = "Require correct GCP/AWS mapping, IKEv2 and versioned write-only keys."
  }
  assert {
    condition     = one(one(google_compute_router.aws.bgp).advertised_ip_ranges).range == "10.30.1.0/24" && length(coalesce(one(google_compute_router.aws.bgp).advertised_groups, [])) == 0 && alltrue([for key, peer in google_compute_router_peer.aws : peer.peer_ip_address == var.vpn_tunnels[key].aws_inside_ip && peer.ip_address == var.vpn_tunnels[key].gcp_inside_ip && peer.advertise_mode == "DEFAULT"])
    error_message = "Advertise only server /24 and use actual BGP hosts."
  }
  assert {
    condition     = alltrue([for tunnel in google_compute_vpn_tunnel.aws : one(one(tunnel.cipher_suite).phase1).encryption == tolist(["AES-CBC-256"]) && one(one(tunnel.cipher_suite).phase2).pfs == tolist(["Group-14"])])
    error_message = "Require one transform compatible with central AWS VPN."
  }
}

run "default_deny_egress" {
  command = plan
  assert {
    condition     = google_compute_firewall.deny_egress.direction == "EGRESS" && google_compute_firewall.deny_egress.destination_ranges == toset(["0.0.0.0/0"]) && one(google_compute_firewall.deny_egress.deny).protocol == "all"
    error_message = "Unsolicited outbound server connections must be denied."
  }
}

run "wrong_project" {
  command = plan
  override_data {
    target = data.google_project.current
    values = { project_id = "another-project", number = "777777777777" }
  }
  expect_failures = [terraform_data.contract]
}
run "wrong_project_number" {
  command = plan
  variables { project_number = "999999999999" }
  expect_failures = [terraform_data.contract]
}
run "wrong_zone" {
  command = plan
  variables { zone = "asia-northeast1-a" }
  expect_failures = [var.zone]
}
run "wrong_image" {
  command = plan
  override_data {
    target          = data.google_compute_image.server
    override_during = plan
    values          = { family = "ubuntu-minimal-2404-lts-amd64", status = "READY" }
  }
  expect_failures = [data.google_compute_image.server]
}
run "reject_public_address_space" {
  command = plan
  variables { address_space = "8.8.0.0/16" }
  expect_failures = [var.address_space]
}
run "reject_subnet_outside" {
  command = plan
  variables { server_subnet_cidr = "10.31.1.0/24" }
  expect_failures = [var.server_subnet_cidr]
}
run "reject_reserved_host" {
  command = plan
  variables { server_private_ip = "10.30.1.1" }
  expect_failures = [var.server_private_ip]
}
run "reject_connected_overlap" {
  command = plan
  variables { connected_cidrs = ["10.0.0.0/8"] }
  expect_failures = [terraform_data.contract]
}
run "reject_unregistered_source" {
  command = plan
  variables { aws_client_cidrs = ["10.99.1.1/32"] }
  expect_failures = [terraform_data.contract]
}
run "reject_default_source" {
  command = plan
  variables { aws_client_cidrs = ["0.0.0.0/0"] }
  expect_failures = [var.aws_client_cidrs]
}
run "reject_partial_tunnels" {
  command = plan
  variables {
    vpn_tunnels           = { for k, t in var.vpn_tunnels : k => t if k != "aws-if1-t2" }
    tunnel_shared_secrets = { for k, v in var.tunnel_shared_secrets : k => v if k != "aws-if1-t2" }
  }
  expect_failures = [var.vpn_tunnels]
}
run "reject_wrong_gateway_mapping" {
  command = plan
  variables { vpn_tunnels = { for k, t in var.vpn_tunnels : k => merge(t, { gcp_interface = 0 }) } }
  expect_failures = [var.vpn_tunnels]
}
run "reject_duplicate_outside_ip" {
  command = plan
  variables { vpn_tunnels = { for k, t in var.vpn_tunnels : k => merge(t, { aws_outside_ip = "3.1.1.1" }) } }
  expect_failures = [var.vpn_tunnels]
}
run "reject_private_outside_ip" {
  command = plan
  variables { vpn_tunnels = merge(var.vpn_tunnels, { aws-if0-t1 = merge(var.vpn_tunnels["aws-if0-t1"], { aws_outside_ip = "10.1.1.1" }) }) }
  expect_failures = [terraform_data.contract]
}
run "reject_invalid_outside_ip" {
  command = plan
  variables { vpn_tunnels = merge(var.vpn_tunnels, { aws-if0-t1 = merge(var.vpn_tunnels["aws-if0-t1"], { aws_outside_ip = "not-an-ip" }) }) }
  expect_failures = [terraform_data.contract]
}
run "reject_duplicate_inside" {
  command = plan
  variables { vpn_tunnels = { for k, t in var.vpn_tunnels : k => merge(t, { inside_cidr = "169.254.10.0/30", aws_inside_ip = "169.254.10.1", gcp_inside_ip = "169.254.10.2" }) } }
  expect_failures = [var.vpn_tunnels]
}
run "reject_reserved_inside" {
  command = plan
  variables { vpn_tunnels = merge(var.vpn_tunnels, { aws-if0-t1 = merge(var.vpn_tunnels["aws-if0-t1"], { inside_cidr = "169.254.0.0/30", aws_inside_ip = "169.254.0.1", gcp_inside_ip = "169.254.0.2" }) }) }
  expect_failures = [var.vpn_tunnels]
}
run "reject_swapped_inside_hosts" {
  command = plan
  variables { vpn_tunnels = merge(var.vpn_tunnels, { aws-if0-t1 = merge(var.vpn_tunnels["aws-if0-t1"], { aws_inside_ip = "169.254.10.2", gcp_inside_ip = "169.254.10.1" }) }) }
  expect_failures = [var.vpn_tunnels]
}
run "reject_same_asn" {
  command = plan
  variables { router_asn = 64512 }
  expect_failures = [var.vpn_tunnels]
}
run "reject_public_asn" {
  command = plan
  variables { router_asn = 1234 }
  expect_failures = [var.router_asn]
}
run "reject_missing_secrets" {
  command = plan
  variables { tunnel_shared_secrets = {} }
  expect_failures = [var.tunnel_shared_secrets]
}
run "reject_weak_secret" {
  command = plan
  variables { tunnel_shared_secrets = { for k, v in var.tunnel_shared_secrets : k => "short" } }
  expect_failures = [var.tunnel_shared_secrets]
}
