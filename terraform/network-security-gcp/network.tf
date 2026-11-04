resource "google_compute_network" "vpc" {
  for_each                        = local.subnet_map
  name                            = "${var.name}-${each.key}"
  auto_create_subnetworks         = false
  routing_mode                    = "GLOBAL"
  bgp_best_path_selection_mode    = "LEGACY"
  mtu                             = 1460
  delete_default_routes_on_create = true
  depends_on                      = [google_project_service.required]
}
resource "google_compute_subnetwork" "subnet" {
  for_each                 = local.subnet_map
  name                     = "${var.name}-${each.key}"
  region                   = var.region
  network                  = google_compute_network.vpc[each.key].id
  ip_cidr_range            = each.value
  stack_type               = "IPV4_ONLY"
  private_ip_google_access = true
}
# Needed for direct replies from the passthrough LB backend; never exported to NCC.
resource "google_compute_route" "lb_return" {
  name             = "${var.name}-lb-return"
  network          = google_compute_network.vpc["workload"].name
  dest_range       = "0.0.0.0/0"
  next_hop_gateway = "default-internet-gateway"
  priority         = 1000
  tags             = ["delta-lb-backend"]
}
resource "google_network_connectivity_hub" "central" {
  name            = "${var.name}-hub"
  policy_mode     = "PRESET"
  preset_topology = "MESH"
  depends_on      = [google_project_service.required]
}
resource "google_network_connectivity_spoke" "workload" {
  name     = "${var.name}-workload"
  hub      = google_network_connectivity_hub.central.id
  location = "global"
  linked_vpc_network {
    uri                   = google_compute_network.vpc["workload"].self_link
    include_export_ranges = [var.subnets.workload]
  }
}
resource "google_compute_ha_vpn_gateway" "aws" {
  name       = "${var.name}-aws"
  region     = var.region
  network    = google_compute_network.vpc["central"].id
  stack_type = "IPV4_ONLY"
}
resource "google_compute_router" "aws" {
  name    = "${var.name}-aws"
  region  = var.region
  network = google_compute_network.vpc["central"].id
  bgp {
    asn            = var.router_asn
    advertise_mode = "CUSTOM"
    advertised_ip_ranges { range = var.subnets.workload }
  }
}
resource "google_compute_external_vpn_gateway" "aws" {
  count           = length(var.aws_vpn_tunnels) == 0 ? 0 : 1
  name            = "${var.name}-aws-peer"
  redundancy_type = "FOUR_IPS_REDUNDANCY"
  dynamic "interface" {
    for_each = var.aws_vpn_tunnels
    content {
      id         = interface.value.aws_interface
      ip_address = interface.value.outside_ip
    }
  }
  depends_on = [terraform_data.contract]
}
resource "google_compute_vpn_tunnel" "aws" {
  for_each                        = var.aws_vpn_tunnels
  name                            = "${var.name}-${each.key}"
  region                          = var.region
  vpn_gateway                     = google_compute_ha_vpn_gateway.aws.id
  vpn_gateway_interface           = each.value.gcp_interface
  peer_external_gateway           = google_compute_external_vpn_gateway.aws[0].id
  peer_external_gateway_interface = each.value.aws_interface
  router                          = google_compute_router.aws.id
  ike_version                     = 2
  shared_secret_wo                = lookup(var.vpn_shared_secrets, each.key, "")
  shared_secret_wo_version        = each.value.secret_version
  lifecycle {
    precondition {
      condition     = contains(keys(var.vpn_shared_secrets), each.key) && length(lookup(var.vpn_shared_secrets, each.key, "")) >= 8
      error_message = "Every tunnel requires an ephemeral pre-shared key."
    }
  }
}
resource "google_compute_router_interface" "aws" {
  for_each   = var.aws_vpn_tunnels
  name       = each.key
  router     = google_compute_router.aws.name
  region     = var.region
  vpn_tunnel = google_compute_vpn_tunnel.aws[each.key].name
  ip_range   = "${each.value.gcp_inside_ip}/30"
}
resource "google_compute_router_peer" "aws" {
  for_each        = var.aws_vpn_tunnels
  name            = each.key
  router          = google_compute_router.aws.name
  region          = var.region
  interface       = google_compute_router_interface.aws[each.key].name
  peer_ip_address = each.value.aws_inside_ip
  peer_asn        = each.value.aws_asn
  advertise_mode  = "CUSTOM"
  advertised_ip_ranges { range = var.subnets.workload }
}
resource "google_network_connectivity_spoke" "aws" {
  count    = length(var.aws_vpn_tunnels) == 0 ? 0 : 1
  name     = "${var.name}-aws"
  location = var.region
  hub      = google_network_connectivity_hub.central.id
  linked_vpn_tunnels {
    uris                       = [for tunnel in google_compute_vpn_tunnel.aws : tunnel.self_link]
    site_to_site_data_transfer = false
    include_export_ranges      = sort(tolist(var.aws_cidrs))
    include_import_ranges      = [var.subnets.workload]
  }
  depends_on = [google_compute_router_peer.aws]
}
