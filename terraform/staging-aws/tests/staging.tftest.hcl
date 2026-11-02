mock_provider "aws" {
  override_during = plan
  mock_data "aws_vpc" {
    defaults = { id = "vpc-00000000000000001", cidr_block = "10.10.0.0/16" }
  }
  mock_data "aws_prefix_list" {
    defaults = { id = "pl-00000000000000001", name = "com.amazonaws.ap-northeast-2.s3" }
  }
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
}

override_data {
  target = data.aws_subnet.selected["subnet-00000000000000001"]
  values = { id = "subnet-00000000000000001", vpc_id = "vpc-00000000000000001", availability_zone_id = "apne2-az1", map_public_ip_on_launch = false }
}
override_data {
  target = data.aws_subnet.selected["subnet-00000000000000002"]
  values = { id = "subnet-00000000000000002", vpc_id = "vpc-00000000000000001", availability_zone_id = "apne2-az2", map_public_ip_on_launch = false }
}

override_resource {
  override_during = plan
  target          = aws_security_group.alb
  values          = { id = "sg-00000000000000001" }
}
override_resource {
  override_during = plan
  target          = aws_security_group.service
  values          = { id = "sg-00000000000000002" }
}

variables {
  account_id      = "444455556666"
  vpc_id          = "vpc-00000000000000001"
  subnet_ids      = ["subnet-00000000000000001", "subnet-00000000000000002"]
  route_table_ids = ["rtb-00000000000000001", "rtb-00000000000000002"]
  scanner_cidrs   = ["10.10.1.0/24"]
}

run "private_staging_foundation" {
  command = plan
  assert {
    condition     = aws_lb.staging.internal && aws_lb.staging.drop_invalid_header_fields && aws_lb.staging.load_balancer_type == "application"
    error_message = "The staging ALB must be internal and drop invalid headers."
  }
  assert {
    condition     = !one(aws_ecs_service.app.network_configuration).assign_public_ip && aws_ecs_service.app.desired_count == 0 && aws_ecs_service.app.launch_type == "FARGATE"
    error_message = "Tasks start at zero on Fargate without public IPs."
  }
  assert {
    condition     = length(aws_vpc_security_group_ingress_rule.alb_from_scanner_cidr) == 1 && aws_vpc_security_group_ingress_rule.alb_from_scanner_cidr["10.10.1.0/24"].from_port == 80 && length(aws_vpc_security_group_ingress_rule.alb_from_scanner_sg) == 0
    error_message = "Only the registered scanner CIDR may reach the ALB listener."
  }
  assert {
    condition     = aws_vpc_security_group_ingress_rule.service_from_alb.referenced_security_group_id == aws_security_group.alb.id && aws_vpc_security_group_ingress_rule.service_from_alb.from_port == 8080
    error_message = "Tasks accept the container port from the ALB security group only."
  }
  assert {
    condition     = length(aws_vpc_endpoint.interface) == 3 && length(aws_vpc_endpoint.s3) == 1 && alltrue([for endpoint in aws_vpc_endpoint.interface : endpoint.private_dns_enabled])
    error_message = "Private subnets need ECR API/DKR, Logs and S3 endpoints to pull images."
  }
  assert {
    condition     = tolist(output.tool_config.allowed_image_prefixes) == tolist(["444455556666.dkr.ecr.ap-northeast-2.amazonaws.com/"]) && output.tool_config.cloud == "aws" && output.tool_config.container_name == "app"
    error_message = "The tool config defaults to this account's ECR only."
  }
}

run "scanner_security_group_without_endpoints" {
  command = plan
  variables {
    scanner_cidrs              = []
    scanner_security_group_ids = ["sg-00000000000000009"]
    create_vpc_endpoints       = false
    route_table_ids            = []
  }
  assert {
    condition     = length(aws_vpc_security_group_ingress_rule.alb_from_scanner_sg) == 1 && length(aws_vpc_security_group_ingress_rule.alb_from_scanner_cidr) == 0
    error_message = "A scanner security group alone may reach the ALB."
  }
  assert {
    condition     = length(aws_vpc_endpoint.interface) == 0 && length(aws_vpc_endpoint.s3) == 0 && length(aws_security_group.endpoints) == 0 && length(aws_vpc_security_group_egress_rule.service_to_s3) == 0
    error_message = "Endpoints are optional for subnets that already have egress."
  }
}

run "rejects_public_scanner_cidr" {
  command = plan
  variables {
    scanner_cidrs = ["0.0.0.0/0"]
  }
  expect_failures = [var.scanner_cidrs]
}

run "rejects_non_canonical_scanner_cidr" {
  command = plan
  variables {
    scanner_cidrs = ["10.10.1.5/24"]
  }
  expect_failures = [var.scanner_cidrs]
}

run "requires_a_scanner_source" {
  command = plan
  variables {
    scanner_cidrs = []
  }
  expect_failures = [terraform_data.contract]
}

run "endpoints_need_route_tables" {
  command = plan
  variables {
    route_table_ids = []
  }
  expect_failures = [terraform_data.contract]
}
