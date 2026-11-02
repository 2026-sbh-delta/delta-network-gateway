terraform {

  required_version = "= 1.16.4"
  required_providers {

    aws = {
      source = "hashicorp/aws", version = "= 6.71.0"
    }

  }
  backend "s3" {

  }

}
provider "aws" {

  region              = var.region
  allowed_account_ids = [var.account_id]
  default_tags {
    tags = {
      Project = "delta-platform", ManagedBy = "terraform", Environment = "hackathon"
    }
  }

}
data "aws_caller_identity" "current" {

}
data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}
data "aws_security_group" "cloudfront" {

  filter {
    name   = "vpc-id"
    values = [aws_vpc.platform.id]
  }
  filter {
    name   = "group-name"
    values = ["CloudFront-VPCOrigins-Service-SG*"]
  }
  depends_on = [aws_cloudfront_vpc_origin.platform]

}
