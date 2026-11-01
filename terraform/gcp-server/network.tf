resource "google_compute_network" "server" {
  name                            = "${var.name}-server"
  auto_create_subnetworks         = false
  routing_mode                    = "REGIONAL"
  delete_default_routes_on_create = true
  enable_ula_internal_ipv6        = false
  mtu                             = 1460
  depends_on                      = [google_project_service.required]
}

resource "google_compute_subnetwork" "server" {
  name                     = "${var.name}-server"
  network                  = google_compute_network.server.id
  region                   = var.region
  ip_cidr_range            = var.server_subnet_cidr
  stack_type               = "IPV4_ONLY"
  private_ip_google_access = false
}

resource "google_compute_address" "server" {
  name         = "${var.name}-server"
  region       = var.region
  subnetwork   = google_compute_subnetwork.server.id
  address_type = "INTERNAL"
  address      = var.server_private_ip
}

resource "google_compute_firewall" "iap_ssh" {
  name          = "${var.name}-iap-ssh"
  network       = google_compute_network.server.id
  direction     = "INGRESS"
  priority      = 1000
  source_ranges = ["35.235.240.0/20"]
  target_tags   = [local.server_tag]
  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

resource "google_compute_firewall" "aws_test" {
  name          = "${var.name}-aws-test"
  network       = google_compute_network.server.id
  direction     = "INGRESS"
  priority      = 1000
  source_ranges = sort(tolist(var.aws_client_cidrs))
  target_tags   = [local.server_tag]
  allow {
    protocol = "tcp"
    ports    = ["8080", "8081"]
  }
}

resource "google_compute_firewall" "deny_egress" {
  name               = "${var.name}-deny-egress"
  network            = google_compute_network.server.id
  direction          = "EGRESS"
  priority           = 1000
  destination_ranges = ["0.0.0.0/0"]
  target_tags        = [local.server_tag]
  deny {
    protocol = "all"
  }
  # GCP stateful connection tracking permits replies to allowed ingress.
  # Platform metadata/IAP paths are distinct from a user-visible default route.
}
