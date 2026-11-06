terraform {
  required_version = "= 1.16.4"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "= 8.9.0"
    }
    aws = {
      source  = "hashicorp/aws"
      version = "= 6.71.0"
    }
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "= 5.28.1"
    }
  }
  backend "gcs" {}
}

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "aws" {
  region = var.aws_region
}

# 토큰은 환경 변수 CLOUDFLARE_API_TOKEN(해당 zone DNS 편집만)으로 받는다. state·변수 파일에 두지 않는다.
provider "cloudflare" {}
