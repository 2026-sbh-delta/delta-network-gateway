provider "aws" {
  region              = var.region
  allowed_account_ids = [var.account_id]
  default_tags {
    tags = local.common_tags
  }
}

data "aws_vpc" "selected" {
  id = var.vpc_id
}

data "aws_subnet" "selected" {
  for_each = var.subnet_ids
  id       = each.value
  lifecycle {
    postcondition {
      condition     = self.vpc_id == var.vpc_id
      error_message = "Every subnet must belong to vpc_id. Tasks never get public IPs regardless of the subnet default."
    }
  }
}

data "aws_prefix_list" "s3" {
  count = var.create_vpc_endpoints ? 1 : 0
  name  = "com.amazonaws.${var.region}.s3"
}
