resource "google_compute_ha_vpn_gateway" "aws" {
  name               = "${var.name}-aws"
  network            = google_compute_network.server.id
  region             = var.region
  stack_type         = "IPV4_ONLY"
  gateway_ip_version = "IPV4"
  labels             = local.labels
  vpn_interfaces {
    id = 0
  }
  vpn_interfaces {
    id = 1
  }
}

resource "google_compute_router" "aws" {
  name    = "${var.name}-aws"
  network = google_compute_network.server.id
  region  = var.region
  bgp {
    asn            = var.router_asn
    advertise_mode = "CUSTOM"
    advertised_ip_ranges {
      range       = var.server_subnet_cidr
      description = "Only the private test server subnet"
    }
  }
}

resource "google_compute_external_vpn_gateway" "aws" {
  count           = length(var.vpn_tunnels) == 0 ? 0 : 1
  name            = "${var.name}-aws-peer"
  redundancy_type = "FOUR_IPS_REDUNDANCY"
  labels          = local.labels
  dynamic "interface" {
    for_each = var.vpn_tunnels
    content {
      id         = interface.value.aws_interface
      ip_address = interface.value.aws_outside_ip
    }
  }
  depends_on = [terraform_data.contract]
}

resource "google_compute_vpn_tunnel" "aws" {
  for_each                        = var.vpn_tunnels
  name                            = "${var.name}-${each.key}"
  region                          = var.region
  vpn_gateway                     = google_compute_ha_vpn_gateway.aws.id
  vpn_gateway_interface           = each.value.gcp_interface
  peer_external_gateway           = google_compute_external_vpn_gateway.aws[0].id
  peer_external_gateway_interface = each.value.aws_interface
  router                          = google_compute_router.aws.id
  ike_version                     = 2
  shared_secret_wo                = var.tunnel_shared_secrets[each.key]
  shared_secret_wo_version        = each.value.secret_version
  labels                          = local.labels
  cipher_suite {
    phase1 {
      encryption = ["AES-CBC-256"]
      integrity  = ["HMAC-SHA2-256-128"]
      prf        = ["PRF-HMAC-SHA2-256"]
      dh         = ["Group-14"]
    }
    phase2 {
      encryption = ["AES-CBC-256"]
      integrity  = ["HMAC-SHA2-256-128"]
      pfs        = ["Group-14"]
    }
  }
}

resource "google_compute_router_interface" "aws" {
  for_each   = var.vpn_tunnels
  name       = "${var.name}-${each.key}"
  router     = google_compute_router.aws.name
  region     = var.region
  ip_range   = "${each.value.gcp_inside_ip}/30"
  vpn_tunnel = google_compute_vpn_tunnel.aws[each.key].name
}

resource "google_compute_router_peer" "aws" {
  for_each                  = var.vpn_tunnels
  name                      = "${var.name}-${each.key}"
  router                    = google_compute_router.aws.name
  region                    = var.region
  interface                 = google_compute_router_interface.aws[each.key].name
  ip_address                = each.value.gcp_inside_ip
  peer_ip_address           = each.value.aws_inside_ip
  peer_asn                  = each.value.aws_bgp_asn
  advertised_route_priority = 100
  advertise_mode            = "DEFAULT"
  enable                    = true
}
