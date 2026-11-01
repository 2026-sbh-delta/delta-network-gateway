# Isolated mock Plans only, no real AWS mutation.
mock_provider "aws" {
  override_during = plan
  mock_data "aws_caller_identity" { defaults = { account_id = "444455556666" } }
  mock_data "aws_vpc" { defaults = { owner_id = "444455556666", enable_dns_support = true, enable_dns_hostnames = true, cidr_block = "10.20.0.0/16" } }
  mock_data "aws_instance" {
    defaults = {
      id                     = "i-00000000000000001", arn = "arn:aws:ec2:ap-northeast-2:444455556666:instance/i-00000000000000001"
      subnet_id              = "subnet-00000000000000001", instance_state = "running", private_ip = "10.20.1.10"
      private_dns            = "ip-10-20-1-10.ap-northeast-2.compute.internal", public_ip = "", ipv6_addresses = []
      vpc_security_group_ids = ["sg-00000000000000001"]
    }
  }
  mock_data "aws_subnet" { defaults = { vpc_id = "vpc-00000000000000001", map_public_ip_on_launch = false } }
  mock_data "aws_security_group" { defaults = { vpc_id = "vpc-00000000000000001" } }
  mock_data "aws_ec2_managed_prefix_list" { defaults = { id = "pl-00000001" } }
  mock_data "aws_ec2_transit_gateway" {
    defaults = { id = "tgw-00000000000000001", owner_id = "111122223333", default_route_table_association = "disable", default_route_table_propagation = "disable" }
  }
  mock_resource "aws_lb" { defaults = { arn = "arn:aws:elasticloadbalancing:ap-northeast-2:444455556666:loadbalancer/app/delta-test/1234567890123456", dns_name = "internal-demo.ap-northeast-2.elb.amazonaws.com" } }
  mock_resource "aws_lb_target_group" { defaults = { arn = "arn:aws:elasticloadbalancing:ap-northeast-2:444455556666:targetgroup/delta-test/1234567890123456" } }
  mock_resource "aws_lb_listener" { defaults = { arn = "arn:aws:elasticloadbalancing:ap-northeast-2:444455556666:listener/app/delta-test/1234567890123456/1234567890123456" } }
  mock_resource "aws_s3_bucket" { defaults = { arn = "arn:aws:s3:::delta-test", bucket_regional_domain_name = "delta-test.s3.ap-northeast-2.amazonaws.com" } }
  mock_resource "aws_cloudfront_distribution" { defaults = { arn = "arn:aws:cloudfront::444455556666:distribution/E1234567890123" } }
}
override_data {
  override_during = plan
  target          = data.aws_vpc.ingress
  values          = { id = "vpc-00000000000000002", owner_id = "444455556666", enable_dns_support = true, enable_dns_hostnames = true, cidr_block = "10.10.0.0/16" }
}
override_data {
  override_during = plan
  target          = data.aws_availability_zone.alb["apne2-az1"]
  values          = { zone_id = "apne2-az1", name = "ap-northeast-2a", region = "ap-northeast-2", zone_type = "availability-zone" }
}
override_data {
  override_during = plan
  target          = data.aws_availability_zone.alb["apne2-az2"]
  values          = { zone_id = "apne2-az2", name = "ap-northeast-2b", region = "ap-northeast-2", zone_type = "availability-zone" }
}
override_resource {
  target          = aws_cloudfront_vpc_origin.alb
  override_during = plan
  values          = { id = "vo_abcdefghijklmnop" }
}
override_resource {
  target          = aws_cloudfront_origin_access_control.site
  override_during = plan
  values          = { id = "E1234567890123" }
}
run "inspected_alb_demo" {
  command = plan
  assert {
    condition     = aws_lb.alb.internal && aws_lb.alb.load_balancer_type == "application" && length(aws_subnet.alb) == 2 && alltrue([for s in aws_subnet.alb : !s.map_public_ip_on_launch && !s.assign_ipv6_address_on_creation])
    error_message = "Use an internal IPv4 ALB in two private subnets."
  }
  assert {
    condition     = length(aws_route.alb_to_server) == 2 && alltrue([for r in aws_route.alb_to_server : r.destination_cidr_block == "10.20.0.0/16" && r.transit_gateway_id == var.transit_gateway_id && r.gateway_id == null]) && aws_internet_gateway.ingress.vpc_id == var.ingress_vpc_id
    error_message = "Route to Server only through TGW, with no Internet gateway route."
  }
  assert {
    condition     = aws_lb_target_group.test["allow"].port == 8080 && aws_lb_target_group.test["deny"].port == 8081 && alltrue([for t in aws_lb_target_group.test : t.target_type == "ip" && t.health_check[0].port == "8080" && t.health_check[0].path == "/health"]) && alltrue([for t in aws_lb_target_group_attachment.server : t.target_id == var.expected_server_private_ip && t.availability_zone == "all"])
    error_message = "Both target groups must health-check allowed 8080 while the deny request still targets 8081."
  }
  assert {
    condition     = length(aws_vpc_security_group_ingress_rule.server_from_alb) == 4 && alltrue([for r in aws_vpc_security_group_ingress_rule.server_from_alb : contains(values(var.alb_subnets), r.cidr_ipv4) && contains([8080, 8081], r.from_port) && r.from_port == r.to_port]) && alltrue([for r in aws_vpc_security_group_egress_rule.alb : r.cidr_ipv4 == "10.20.1.10/32" && contains([8080, 8081], r.from_port)])
    error_message = "SGs must admit both control ports so only central ANF denies 8081."
  }
  assert {
    condition     = aws_cloudfront_vpc_origin.alb.vpc_origin_endpoint_config[0].arn == aws_lb.alb.arn && aws_cloudfront_vpc_origin.alb.vpc_origin_endpoint_config[0].http_port == 80 && aws_cloudfront_vpc_origin.alb.vpc_origin_endpoint_config[0].origin_protocol_policy == "http-only" && toset([for o in aws_cloudfront_distribution.server.origin : o.origin_id]) == toset(["private-static-site", "inspected-alb-api"]) && alltrue([for o in aws_cloudfront_distribution.server.origin : length(o.custom_origin_config) == 0])
    error_message = "Use only private S3 and ALB origins in the distribution."
  }
  assert {
    condition     = aws_cloudfront_distribution.server.default_cache_behavior[0].target_origin_id == "private-static-site" && aws_cloudfront_distribution.server.default_root_object == "index.html" && length(aws_cloudfront_distribution.server.ordered_cache_behavior) == 2 && alltrue([for b in aws_cloudfront_distribution.server.ordered_cache_behavior : b.target_origin_id == "inspected-alb-api" && b.viewer_protocol_policy == "redirect-to-https" && b.cache_policy_id == "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"]) && alltrue([for e in aws_cloudfront_distribution.server.custom_error_response : e.error_caching_min_ttl == 0 && e.response_code == null])
    error_message = "Serve the page from S3, API through ALB, without API or error caching or 200 error remapping."
  }
  assert {
    condition     = aws_s3_bucket_public_access_block.site.block_public_acls && aws_s3_bucket_public_access_block.site.block_public_policy && aws_s3_bucket_public_access_block.site.ignore_public_acls && aws_s3_bucket_public_access_block.site.restrict_public_buckets && aws_cloudfront_origin_access_control.site.signing_behavior == "always" && jsondecode(aws_s3_bucket_policy.site.policy).Statement[0].Condition.StringEquals["AWS:SourceArn"] == aws_cloudfront_distribution.server.arn
    error_message = "The site bucket must be private and OAC restricted to this Distribution ARN."
  }
  assert {
    condition     = output.central_allowed_flow.cloudfront_alb_to_server.destination_ports == [8080] && output.central_allowed_flow.cloudfront_alb_to_server.sid == 1000002 && aws_vpc_security_group_ingress_rule.alb_cloudfront_service.cidr_ipv4 == null && aws_vpc_security_group_ingress_rule.alb_cloudfront_service.from_port == 80 && length(aws_vpc_security_group_ingress_rule.alb_bootstrap) == 1
    error_message = "Central policy permits only 8080; bootstrap and service SG ingress are only ALB port 80."
  }
}
run "hardened_service_sg_only" {
  command = plan
  variables { bootstrap_origin_access = false }
  assert {
    condition     = length(aws_vpc_security_group_ingress_rule.alb_bootstrap) == 0 && aws_vpc_security_group_ingress_rule.alb_cloudfront_service.cidr_ipv4 == null && aws_vpc_security_group_ingress_rule.alb_cloudfront_service.cidr_ipv6 == null
    error_message = "Remove the temporary prefix-list rule after service SG creation."
  }
}
run "wrong_authenticated_account" {
  command = plan
  variables { workload_account_id = "777788889999" }
  expect_failures = [terraform_data.contract]
}
run "wrong_server_vpc" {
  command = plan
  variables { vpc_id = "vpc-00000000000000003" }
  expect_failures = [terraform_data.contract]
}
run "unattached_server_sg" {
  command = plan
  variables { server_security_group_id = "sg-00000000000000003" }
  expect_failures = [terraform_data.contract]
}
run "unexpected_private_ip" {
  command = plan
  variables { expected_server_private_ip = "10.20.1.11" }
  expect_failures = [terraform_data.contract]
}
run "public_server_ip_rejected" {
  command = plan
  override_data {
    target          = data.aws_instance.server
    override_during = plan
    values = {
      id                     = "i-00000000000000001", arn = "arn:aws:ec2:ap-northeast-2:444455556666:instance/i-00000000000000001"
      subnet_id              = "subnet-00000000000000001", instance_state = "running", private_ip = "10.20.1.10"
      private_dns            = "ip-10-20-1-10.ap-northeast-2.compute.internal", public_ip = "203.0.113.10", ipv6_addresses = []
      vpc_security_group_ids = ["sg-00000000000000001"]
    }
  }
  expect_failures = [terraform_data.contract]
}
run "ipv6_server_rejected" {
  command = plan
  override_data {
    target          = data.aws_instance.server
    override_during = plan
    values = {
      id                     = "i-00000000000000001", arn = "arn:aws:ec2:ap-northeast-2:444455556666:instance/i-00000000000000001"
      subnet_id              = "subnet-00000000000000001", instance_state = "running", private_ip = "10.20.1.10"
      private_dns            = "ip-10-20-1-10.ap-northeast-2.compute.internal", public_ip = "", ipv6_addresses = ["2001:db8::1"]
      vpc_security_group_ids = ["sg-00000000000000001"]
    }
  }
  expect_failures = [terraform_data.contract]
}
run "wrong_region" {
  command = plan
  variables { region = "us-east-1" }
  expect_failures = [var.region]
}
run "invalid_account_id" {
  command = plan
  variables { workload_account_id = "123" }
  expect_failures = [var.workload_account_id]
}
run "invalid_instance_id" {
  command = plan
  variables { server_instance_id = "i-short" }
  expect_failures = [var.server_instance_id]
}
run "invalid_vpc_id" {
  command = plan
  variables { vpc_id = "vpc-short" }
  expect_failures = [var.vpc_id]
}
run "invalid_sg_id" {
  command = plan
  variables { server_security_group_id = "sg-short" }
  expect_failures = [var.server_security_group_id]
}
run "public_expected_ip" {
  command = plan
  variables { expected_server_private_ip = "203.0.113.10" }
  expect_failures = [var.expected_server_private_ip]
}
run "invalid_expected_ip" {
  command = plan
  variables { expected_server_private_ip = "10.20.1.999" }
  expect_failures = [var.expected_server_private_ip]
}
run "invalid_name" {
  command = plan
  variables { name = "Invalid_Name" }
  expect_failures = [var.name]
}

run "overlapping_alb_subnets" {
  command = plan
  variables { alb_subnets = { apne2-az1 = "10.10.4.0/24", apne2-az2 = "10.10.4.0/24" } }
  expect_failures = [terraform_data.contract]
}
run "outside_ingress_vpc" {
  command = plan
  variables { alb_subnets = { apne2-az1 = "10.30.4.0/24", apne2-az2 = "10.10.5.0/24" } }
  expect_failures = [terraform_data.contract]
}
run "incorrect_subnet_size" {
  command = plan
  variables { alb_subnets = { apne2-az1 = "10.10.4.0/25", apne2-az2 = "10.10.5.0/24" } }
  expect_failures = [var.alb_subnets]
}
run "wrong_central_owner" {
  command = plan
  variables { central_account_id = "777788889999" }
  expect_failures = [terraform_data.contract]
}
run "wrong_az_region" {
  command = plan
  override_data {
    override_during = plan
    target          = data.aws_availability_zone.alb["apne2-az1"]
    values          = { zone_id = "apne2-az1", region = "us-east-1", zone_type = "availability-zone" }
  }
  expect_failures = [terraform_data.contract]
}
