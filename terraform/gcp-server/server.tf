resource "google_compute_instance" "server" {
  name                = "${var.name}-server"
  zone                = var.zone
  machine_type        = "e2-micro"
  can_ip_forward      = false
  deletion_protection = true
  labels              = local.labels
  tags                = [local.server_tag]
  boot_disk {
    auto_delete = true
    initialize_params {
      image = data.google_compute_image.server.self_link
      size  = max(10, data.google_compute_image.server.disk_size_gb)
      type  = "pd-standard"
    }
  }
  network_interface {
    subnetwork = google_compute_subnetwork.server.id
    network_ip = google_compute_address.server.address
    stack_type = "IPV4_ONLY"
    # No access_config or ipv6_access_config: no external IP.
  }
  metadata = {
    enable-oslogin           = "TRUE"
    block-project-ssh-keys   = "TRUE"
    disable-legacy-endpoints = "TRUE"
  }
  metadata_startup_script = templatefile("${path.module}/templates/startup.sh.tftpl", {
    app_base64 = filebase64("${path.module}/app/server.py")
  })
  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }
  # No service_account block: the server needs no Google API credentials.
  depends_on = [google_compute_firewall.iap_ssh, google_compute_firewall.aws_test, google_compute_firewall.deny_egress]
}
