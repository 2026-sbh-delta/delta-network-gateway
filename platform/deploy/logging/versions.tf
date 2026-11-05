terraform {
  required_version = "= 1.16.4"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "= 6.71.0" }
    google = { source = "hashicorp/google", version = "= 8.10.0" }
  }
  backend "s3" {}
}
provider "aws" {
  region              = var.aws_region
  allowed_account_ids = [var.aws_account_id]
  default_tags { tags = { Project = "delta-platform", Component = "central-logging", ManagedBy = "terraform" } }
}
provider "google" { project = var.gcp_project_id }
