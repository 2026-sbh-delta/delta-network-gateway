terraform {
  required_version = ">= 1.11, < 2.0"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 7.0" }
  }
}
variable "project_id" { type = string }
variable "name" { type = string }
provider "google" {
  project = var.project_id
  region  = "asia-northeast3"
}
# Temporary builder in the existing default network, with no service account.
resource "google_compute_firewall" "builder" {
  name          = var.name
  network       = "default"
  direction     = "INGRESS"
  priority      = 100
  source_ranges = ["0.0.0.0/0"]
  target_tags   = [var.name]
  deny { protocol = "all" }
}
resource "google_compute_firewall" "private" {
  name               = "${var.name}-private"
  network            = "default"
  direction          = "EGRESS"
  priority           = 100
  destination_ranges = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
  target_tags        = [var.name]
  deny { protocol = "all" }
}
resource "google_compute_instance" "builder" {
  name         = var.name
  zone         = "asia-northeast3-a"
  machine_type = "e2-small"
  boot_disk {
    initialize_params {
      image = "projects/debian-cloud/global/images/debian-12-bookworm-v20261102"
      size  = 12
      type  = "pd-balanced"
    }
  }
  network_interface {
    subnetwork = "projects/${var.project_id}/regions/asia-northeast3/subnetworks/default"
    access_config {}
  }
  tags                    = [var.name]
  depends_on              = [google_compute_firewall.builder, google_compute_firewall.private]
  metadata                = { enable-oslogin = "TRUE", block-project-ssh-keys = "TRUE" }
  metadata_startup_script = file("${path.module}/bootstrap.sh")
  labels                  = { managed-by = "delta-platform", purpose = "runtime-image-build" }
}
