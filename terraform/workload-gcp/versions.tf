terraform {
  required_version = "= 1.16.4"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "= 8.10.0"
    }
  }
  backend "s3" {}
}
provider "google" {
  project        = var.project_id
  region         = var.region
  zone           = var.zone
  default_labels = { team = "delta", environment = "lab", managed_by = "terraform" }
}
