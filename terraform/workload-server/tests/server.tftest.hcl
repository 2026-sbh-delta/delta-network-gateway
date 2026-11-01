mock_provider "aws" {
  override_during = plan
  mock_data "aws_caller_identity" {
    defaults = { account_id = "444455556666" }
  }
  mock_data "aws_ami" {
    defaults = {
      id                  = "ami-00000000000000001"
      architecture        = "arm64"
      name                = "al2023-ami-standard-arm64"
      root_device_type    = "ebs"
      virtualization_type = "hvm"
      state               = "available"
    }
  }
  mock_data "aws_ec2_transit_gateway" {
    defaults = {
      id                              = "tgw-00000000000000001"
      owner_id                        = "111122223333"
      default_route_table_association = "disable"
      default_route_table_propagation = "disable"
      auto_accept_shared_attachments  = "disable"
    }
  }
}

override_data {
  target = data.aws_availability_zone.selected["apne2-az1"]
  values = { zone_id = "apne2-az1", name = "ap-northeast-2a", region = "ap-northeast-2", zone_type = "availability-zone" }
}
override_data {
  target = data.aws_availability_zone.selected["apne2-az2"]
  values = { zone_id = "apne2-az2", name = "ap-northeast-2b", region = "ap-northeast-2", zone_type = "availability-zone" }
}
override_resource {
  override_during = plan
  target          = aws_security_group.server
  values          = { id = "sg-00000000000000001" }
}
override_resource {
  override_during = plan
  target          = aws_security_group.management
  values          = { id = "sg-00000000000000002" }
}

override_resource {
  override_during = plan
  target          = aws_subnet.private["apne2-az1/server"]
  values          = { id = "subnet-00000000000000001" }
}
override_resource {
  override_during = plan
  target          = aws_route_table.private["apne2-az1/server"]
  values          = { id = "rtb-00000000000000001" }
}
override_resource {
  override_during = plan
  target          = aws_subnet.private["apne2-az1/tgw"]
  values          = { id = "subnet-00000000000000002" }
}
override_resource {
  override_during = plan
  target          = aws_route_table.private["apne2-az1/tgw"]
  values          = { id = "rtb-00000000000000002" }
}
override_resource {
  override_during = plan
  target          = aws_subnet.private["apne2-az2/server"]
  values          = { id = "subnet-00000000000000003" }
}
override_resource {
  override_during = plan
  target          = aws_route_table.private["apne2-az2/server"]
  values          = { id = "rtb-00000000000000003" }
}
override_resource {
  override_during = plan
  target          = aws_subnet.private["apne2-az2/tgw"]
  values          = { id = "subnet-00000000000000004" }
}
override_resource {
  override_during = plan
  target          = aws_route_table.private["apne2-az2/tgw"]
  values          = { id = "rtb-00000000000000004" }
}

run "private_foundation" {
  command = plan
  assert {
    condition     = length(aws_subnet.private) == 4 && length(aws_route_table.private) == 4 && length(aws_route_table_association.private) == 4
    error_message = "The private foundation needs four dedicated subnets and explicit route-table associations."
  }
  assert {
    condition     = !aws_instance.server.associate_public_ip_address && aws_instance.server.ipv6_address_count == 0 && !aws_vpc.server.assign_generated_ipv6_cidr_block && alltrue([for subnet in aws_subnet.private : !subnet.map_public_ip_on_launch && !subnet.assign_ipv6_address_on_creation])
    error_message = "The server and all subnets must have neither public IPv4 nor IPv6 allocation."
  }
  assert {
    condition     = one(aws_instance.server.metadata_options).http_tokens == "required" && one(aws_instance.server.root_block_device).encrypted && one(aws_instance.server.root_block_device).volume_size == 8 && one(aws_instance.server.credit_specification).cpu_credits == "standard"
    error_message = "IMDSv2, encrypted 8GiB EBS and Standard credits are required."
  }
  assert {
    condition     = aws_ec2_instance_connect_endpoint.management.ip_address_type == "ipv4" && !aws_ec2_instance_connect_endpoint.management.preserve_client_ip && aws_ec2_instance_connect_endpoint.management.subnet_id == aws_instance.server.subnet_id
    error_message = "Management must use the private endpoint in the server AZ."
  }
  assert {
    condition     = aws_vpc_security_group_ingress_rule.ssh.referenced_security_group_id == aws_security_group.management.id && aws_vpc_security_group_ingress_rule.ssh.from_port == 22 && aws_vpc_security_group_ingress_rule.ssh.to_port == 22 && aws_vpc_security_group_egress_rule.management.referenced_security_group_id == aws_security_group.server.id
    error_message = "SSH must be restricted to the endpoint SG and its server SG target."
  }
  assert {
    condition     = length(aws_ec2_transit_gateway_vpc_attachment.server) == 0 && length(aws_route.remote) == 0 && length(aws_vpc_security_group_ingress_rule.test) == 0 && length(output.central_handoff) == 0
    error_message = "The initial server must be independently deployable without a TGW, remote routes or test egress."
  }
}

run "registered_remote_via_tgw" {
  command = plan
  variables {
    transit_gateway_id   = "tgw-00000000000000001"
    remote_cidrs         = ["10.30.0.0/16"]
    allowed_client_cidrs = ["10.30.1.1/32"]
    enable_remote_routes = true
  }
  assert {
    condition     = one(aws_ec2_transit_gateway_vpc_attachment.server).subnet_ids == toset([aws_subnet.private["apne2-az1/tgw"].id, aws_subnet.private["apne2-az2/tgw"].id]) && !one(aws_ec2_transit_gateway_vpc_attachment.server).transit_gateway_default_route_table_association && !one(aws_ec2_transit_gateway_vpc_attachment.server).transit_gateway_default_route_table_propagation
    error_message = "Only the dedicated AZ attachment subnets are selected and central association/propagation remains explicit."
  }
  assert {
    condition     = length(aws_route.remote) == 2 && alltrue([for key, route in aws_route.remote : route.transit_gateway_id == var.transit_gateway_id && route.destination_cidr_block == "10.30.0.0/16" && route.route_table_id == aws_route_table.private["${local.routes[key].az_id}/server"].id])
    error_message = "Both server subnets must send only the registered remote prefix to TGW."
  }
  assert {
    condition     = length(aws_vpc_security_group_ingress_rule.test) == 2 && alltrue([for rule in aws_vpc_security_group_ingress_rule.test : rule.cidr_ipv4 == "10.30.1.1/32" && contains([8080, 8081], rule.from_port) && rule.from_port == rule.to_port])
    error_message = "Test egress must include both controls only for the registered private network."
  }
  assert {
    condition     = output.central_handoff.aws_server.role == "Backend" && output.central_handoff.aws_server.acceptance_mode == "accept" && output.central_handoff.aws_server.account_id == var.workload_account_id
    error_message = "The stable central handoff must identify the Backend role and central acceptance ownership."
  }
}

run "wrong_authenticated_account" {
  command = plan
  override_data {
    target = data.aws_caller_identity.current
    values = { account_id = "777788889999" }
  }
  expect_failures = [terraform_data.contract]
}

run "wrong_region_az" {
  command = plan
  override_data {
    target = data.aws_availability_zone.selected["apne2-az1"]
    values = { zone_id = "apne2-az1", region = "us-east-1", zone_type = "availability-zone" }
  }
  expect_failures = [terraform_data.contract]
}

run "reject_wrong_ami" {
  command = plan
  override_data {
    target = data.aws_ami.server
    values = { id = "ami-00000000000000001", name = "al2023-ami-minimal-arm64", architecture = "arm64", root_device_type = "ebs", virtualization_type = "hvm", state = "available" }
  }
  expect_failures = [data.aws_ami.server]
}

run "reject_wrong_tgw_owner" {
  command = plan
  variables {
    transit_gateway_id = "tgw-00000000000000001"
  }
  override_data {
    target = data.aws_ec2_transit_gateway.central[0]
    values = { id = "tgw-00000000000000001", owner_id = "777788889999" }
  }
  expect_failures = [data.aws_ec2_transit_gateway.central]
}

run "reject_account_format" {
  command = plan
  variables {
    workload_account_id = "invalid"
  }
  expect_failures = [var.workload_account_id]
}

run "reject_same_central_account" {
  command = plan
  variables {
    central_account_id = "444455556666"
  }
  expect_failures = [var.central_account_id]
}

run "reject_noncanonical_vpc" {
  command = plan
  variables {
    vpc_cidr = "10.20.1.0/16"
  }
  expect_failures = [var.vpc_cidr]
}

run "reject_public_vpc" {
  command = plan
  variables {
    vpc_cidr = "8.8.0.0/16"
  }
  expect_failures = [terraform_data.contract]
}

run "reject_no_attachment_route" {
  command = plan
  variables {
    remote_cidrs = ["10.30.0.0/16"]
  }
  expect_failures = [var.remote_cidrs]
}

run "reject_default_route" {
  command = plan
  variables {
    transit_gateway_id = "tgw-00000000000000001"
    remote_cidrs       = ["0.0.0.0/0"]
  }
  expect_failures = [terraform_data.contract]
}

run "reject_public_remote" {
  command = plan
  variables {
    transit_gateway_id = "tgw-00000000000000001"
    remote_cidrs       = ["8.8.8.0/24"]
  }
  expect_failures = [terraform_data.contract]
}

run "reject_remote_overlapping_workload" {
  command = plan
  variables {
    transit_gateway_id = "tgw-00000000000000001"
    remote_cidrs       = ["10.20.1.0/24"]
  }
  expect_failures = [terraform_data.contract]
}

run "reject_inspection_containment" {
  command = plan
  variables {
    inspection_cidrs = ["10.0.0.0/8"]
  }
  expect_failures = [terraform_data.contract]
}

run "reject_remote_overlap" {
  command = plan
  variables {
    transit_gateway_id = "tgw-00000000000000001"
    remote_cidrs       = ["10.30.0.0/16", "10.30.1.0/24"]
  }
  expect_failures = [terraform_data.contract]
}

run "reject_wrong_az_set" {
  command = plan
  variables {
    central_az_ids = ["apne2-az3", "apne2-az4"]
  }
  expect_failures = [var.azs]
}

run "reject_broad_client_source" {
  command = plan
  variables { allowed_client_cidrs = ["10.30.0.0/16"] }
  expect_failures = [var.allowed_client_cidrs]
}

run "reject_bad_ami_id" {
  command = plan
  variables {
    ami_id = "unknown"
  }
  expect_failures = [var.ami_id]
}

run "reject_bad_tgw_id" {
  command = plan
  variables {
    transit_gateway_id = "invalid"
  }
  expect_failures = [var.transit_gateway_id]
}

run "reject_duplicate_subnet" {
  command = plan
  variables {
    server_private_ip = "10.20.0.10"
    azs = {
      apne2-az1 = { tgw_subnet_cidr = "10.20.0.0/24", server_subnet_cidr = "10.20.0.0/24" }
      apne2-az2 = { tgw_subnet_cidr = "10.20.2.0/24", server_subnet_cidr = "10.20.3.0/24" }
    }
  }
  expect_failures = [terraform_data.contract]
}

run "reject_subnet_outside_vpc" {
  command = plan
  variables {
    azs = {
      apne2-az1 = { tgw_subnet_cidr = "10.11.0.0/24", server_subnet_cidr = "10.20.1.0/24" }
      apne2-az2 = { tgw_subnet_cidr = "10.20.2.0/24", server_subnet_cidr = "10.20.3.0/24" }
    }
  }
  expect_failures = [terraform_data.contract]
}

run "reject_subnet_prefix" {
  command = plan
  variables {
    azs = {
      apne2-az1 = { tgw_subnet_cidr = "10.20.0.0/23", server_subnet_cidr = "10.20.1.0/24" }
      apne2-az2 = { tgw_subnet_cidr = "10.20.2.0/24", server_subnet_cidr = "10.20.3.0/24" }
    }
  }
  expect_failures = [var.azs]
}

run "reject_enabled_default_route_table_association" {
  command = plan
  variables {
    transit_gateway_id = "tgw-00000000000000001"
  }
  override_data {
    target = data.aws_ec2_transit_gateway.central[0]
    values = {
      id                              = "tgw-00000000000000001"
      owner_id                        = "111122223333"
      default_route_table_association = "enable"
      default_route_table_propagation = "disable"
      auto_accept_shared_attachments  = "disable"
    }
  }
  expect_failures = [data.aws_ec2_transit_gateway.central]
}

run "reject_enabled_default_route_table_propagation" {
  command = plan
  variables {
    transit_gateway_id = "tgw-00000000000000001"
  }
  override_data {
    target = data.aws_ec2_transit_gateway.central[0]
    values = {
      id                              = "tgw-00000000000000001"
      owner_id                        = "111122223333"
      default_route_table_association = "disable"
      default_route_table_propagation = "enable"
      auto_accept_shared_attachments  = "disable"
    }
  }
  expect_failures = [data.aws_ec2_transit_gateway.central]
}

run "reject_enabled_auto_accept_shared_attachments" {
  command = plan
  variables {
    transit_gateway_id = "tgw-00000000000000001"
  }
  override_data {
    target = data.aws_ec2_transit_gateway.central[0]
    values = {
      id                              = "tgw-00000000000000001"
      owner_id                        = "111122223333"
      default_route_table_association = "disable"
      default_route_table_propagation = "disable"
      auto_accept_shared_attachments  = "enable"
    }
  }
  expect_failures = [data.aws_ec2_transit_gateway.central]
}

run "pending_central_acceptance" {
  command = plan
  variables {
    transit_gateway_id   = "tgw-00000000000000001"
    remote_cidrs         = ["10.10.0.0/16"]
    allowed_client_cidrs = ["10.10.1.143/32"]
  }
  assert {
    condition     = length(aws_ec2_transit_gateway_vpc_attachment.server) == 1 && length(aws_route.remote) == 0 && length(aws_vpc_security_group_ingress_rule.test) == 2
    error_message = "Attachment and both listeners can be prepared before enabling return routes."
  }
}
run "reject_unregistered_client_source" {
  command = plan
  variables { allowed_client_cidrs = ["10.99.1.1/32"] }
  expect_failures = [terraform_data.contract]
}
run "reject_reserved_server_ip" {
  command = plan
  variables { server_private_ip = "10.20.1.3" }
  expect_failures = [var.server_private_ip]
}
run "reject_server_ip_outside_selected_subnet" {
  command = plan
  variables { server_private_ip = "10.20.3.10" }
  expect_failures = [var.server_private_ip]
}
run "reject_routes_without_remote" {
  command = plan
  variables { enable_remote_routes = true }
  expect_failures = [var.enable_remote_routes]
}
