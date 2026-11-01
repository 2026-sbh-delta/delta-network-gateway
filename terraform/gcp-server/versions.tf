terraform {
  required_version = "= 1.16.4"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "= 8.9.0"
    }
  }
  backend "gcs" {}
}
