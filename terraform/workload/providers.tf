provider "aws" {
  region              = var.region
  allowed_account_ids = [var.workload_account_id]
  default_tags {
    tags = local.common_tags
  }
}

data "aws_caller_identity" "current" {}

data "aws_availability_zone" "selected" {
  for_each = var.azs
  zone_id  = each.key
  state    = "available"
}

data "aws_ec2_transit_gateway" "central" {
  count = var.transit_gateway_id == null ? 0 : 1
  id    = var.transit_gateway_id
  lifecycle {
    postcondition {
      condition     = self.owner_id == var.central_account_id
      error_message = "The shared TGW must belong to central_account_id."
    }
    postcondition {
      condition = (
        self.default_route_table_association == "disable" && self.default_route_table_propagation == "disable" &&
        self.auto_accept_shared_attachments == "disable"
      )
      error_message = "The central TGW must disable default association, propagation and automatic shared attachment acceptance."
    }
  }
}
