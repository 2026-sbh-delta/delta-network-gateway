locals {
  prefix = "${var.name}-${var.environment}-staging"
  common_tags = merge({
    Team        = "Delta"
    Project     = "delta-multicloud"
    Environment = var.environment
    ManagedBy   = "Terraform"
    Component   = "staging"
  }, var.tags)
  ecr_prefix             = "${var.account_id}.dkr.ecr.${var.region}.amazonaws.com/"
  allowed_image_prefixes = length(var.allowed_image_prefixes) > 0 ? var.allowed_image_prefixes : [local.ecr_prefix]
  interface_endpoints    = var.create_vpc_endpoints ? toset(["ecr.api", "ecr.dkr", "logs"]) : toset([])
}

resource "terraform_data" "contract" {
  input = {
    scanner_sources = length(var.scanner_cidrs) + length(var.scanner_security_group_ids) + length(var.scanner_public_cidrs)
  }
  lifecycle {
    precondition {
      condition     = !var.dual_container || var.create_vpc_endpoints || var.existing_nat_egress
      error_message = "Dual DB mode requires Secrets Manager HTTPS through VPC endpoints or explicitly attested existing NAT routes."
    }
    precondition {
      condition     = var.alb_internet_facing || length(var.scanner_cidrs) + length(var.scanner_security_group_ids) > 0
      error_message = "Set scanner_cidrs or scanner_security_group_ids; otherwise nothing can reach the internal ALB."
    }
    precondition {
      condition     = var.alb_internet_facing || length(var.scanner_public_cidrs) == 0
      error_message = "scanner_public_cidrs only applies to an internet-facing ALB."
    }
    precondition {
      condition     = !var.create_vpc_endpoints || length(var.route_table_ids) > 0
      error_message = "create_vpc_endpoints needs route_table_ids for the S3 gateway endpoint."
    }
    precondition {
      condition     = length(distinct([for subnet in data.aws_subnet.selected : subnet.availability_zone_id])) >= 2
      error_message = "subnet_ids must span at least two AZs for the ALB."
    }
  }
}

locals {
  ingress_container_name = var.dual_container ? "frontend" : var.container_name
  ingress_container_port = var.dual_container ? 3000 : var.container_port
  database_name          = "board_staging"
  database_user          = "board_app"
}
