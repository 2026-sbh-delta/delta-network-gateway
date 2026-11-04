resource "google_compute_instance" "server" {
  name                = "${var.name}-server"
  zone                = var.zone
  machine_type        = "e2-micro"
  deletion_protection = var.deletion_protection
  tags                = ["delta-lb-backend"]
  boot_disk {
    initialize_params {
      image = var.server_image
      size  = 10
      type  = "pd-standard"
    }
  }
  network_interface {
    subnetwork = google_compute_subnetwork.subnet["workload"].id
    network_ip = cidrhost(var.subnets.workload, 10)
    # No access_config, IPv6, NAT or external VM address.
  }
  metadata = {
    enable-oslogin         = "TRUE"
    block-project-ssh-keys = "TRUE"
  }
  metadata_startup_script = templatefile("${path.module}/templates/startup.sh.tftpl", { app = base64encode(file("${path.module}/app/server.py")) })
  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }
  depends_on = [
    google_compute_network_firewall_policy_rule.ingress,
    google_compute_network_firewall_policy_rule.deny_ingress,
    google_compute_network_firewall_policy_rule.private_egress,
    google_compute_network_firewall_policy_association.vpc,
  ]
}
resource "google_compute_instance_group" "server" {
  name      = "${var.name}-server"
  zone      = var.zone
  instances = [google_compute_instance.server.self_link]
}
resource "google_compute_region_health_check" "server" {
  name   = "${var.name}-server"
  region = var.region
  http_health_check {
    port         = 8080
    request_path = "/health"
  }
}
resource "google_compute_region_backend_service" "server" {
  name                  = "${var.name}-server"
  region                = var.region
  protocol              = "TCP"
  load_balancing_scheme = "EXTERNAL"
  health_checks         = [google_compute_region_health_check.server.id]
  backend {
    group          = google_compute_instance_group.server.self_link
    balancing_mode = "CONNECTION"
  }
}
resource "google_compute_address" "demo" {
  name         = "${var.name}-demo"
  region       = var.region
  address_type = "EXTERNAL"
}
resource "google_compute_forwarding_rule" "demo" {
  name                  = "${var.name}-demo"
  region                = var.region
  ip_address            = google_compute_address.demo.address
  ip_protocol           = "TCP"
  ports                 = ["8080", "8081"]
  load_balancing_scheme = "EXTERNAL"
  backend_service       = google_compute_region_backend_service.server.id
}
