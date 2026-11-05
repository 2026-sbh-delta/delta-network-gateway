terraform {
  required_version = "= 1.16.4"
  required_providers {
    google      = { source = "hashicorp/google", version = "= 8.10.0" }
    google-beta = { source = "hashicorp/google-beta", version = "= 8.10.0" }
    aws         = { source = "hashicorp/aws", version = "= 6.71.0" }
  }
  backend "gcs" {}
}
provider "google" {
  project = var.project_id
  region  = var.region
}
provider "google-beta" {
  project = var.project_id
  region  = var.region
}
provider "aws" {
  region              = "ap-northeast-2"
  allowed_account_ids = [var.aws_account_id]
}
