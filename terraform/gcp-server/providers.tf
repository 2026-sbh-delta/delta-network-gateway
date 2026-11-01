provider "google" {
  project               = var.project_id
  region                = var.region
  zone                  = var.zone
  billing_project       = var.project_id
  user_project_override = true
}

data "google_project" "current" {
  project_id = var.project_id
}

data "google_compute_image" "server" {
  project = "ubuntu-os-cloud"
  name    = var.server_image_name
  lifecycle {
    postcondition {
      condition     = self.family == "ubuntu-2404-lts-amd64" && self.status == "READY"
      error_message = "The pinned image must be a ready Ubuntu 24.04 standard AMD64 image."
    }
  }
}

resource "google_project_service" "required" {
  for_each           = toset(["compute.googleapis.com", "iap.googleapis.com", "oslogin.googleapis.com"])
  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
  depends_on         = [terraform_data.contract]
}
