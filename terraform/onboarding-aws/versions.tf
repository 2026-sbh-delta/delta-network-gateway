terraform {
  required_version = "= 1.16.4"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "= 6.71.0"
    }
  }
  backend "s3" {}
}

provider "aws" {
  region              = var.region
  allowed_account_ids = [var.target_account_id]
  default_tags {
    tags = { Project = "delta", Component = "onboarding", ManagedBy = "terraform" }
  }
}
