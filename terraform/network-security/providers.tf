provider "aws" {
  region              = var.region
  allowed_account_ids = [var.central_account_id]

  default_tags {
    tags = local.common_tags
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

data "aws_availability_zone" "selected" {
  for_each = var.inspection_azs
  zone_id  = each.key
  state    = "available"
}
