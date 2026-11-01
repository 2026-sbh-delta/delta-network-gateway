provider "aws" {
  region              = var.region
  allowed_account_ids = [var.workload_account_id]
  default_tags {
    tags = local.common_tags
  }
}
data "aws_caller_identity" "current" {}
data "aws_vpc" "server" {
  id = var.vpc_id
}
data "aws_instance" "server" {
  instance_id = var.server_instance_id
}
data "aws_subnet" "server" {
  id = data.aws_instance.server.subnet_id
}
data "aws_security_group" "server" {
  id = var.server_security_group_id
}
data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}
data "aws_vpc" "ingress" {
  id = var.ingress_vpc_id
}
data "aws_availability_zone" "alb" {
  for_each = var.alb_subnets
  zone_id  = each.key
  state    = "available"
}
data "aws_ec2_transit_gateway" "central" {
  id = var.transit_gateway_id
}
data "aws_security_group" "alb_cloudfront_service" {
  filter {
    name   = "vpc-id"
    values = [var.ingress_vpc_id]
  }
  filter {
    name   = "group-name"
    values = ["CloudFront-VPCOrigins-Service-SG*"]
  }
  depends_on = [aws_cloudfront_vpc_origin.alb]
}
