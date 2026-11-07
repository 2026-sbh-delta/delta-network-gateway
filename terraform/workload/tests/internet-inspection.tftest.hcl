mock_provider "aws" {
  override_during = plan
  mock_data "aws_ami" {
    defaults = { id = "ami-00000000000000001", architecture = "x86_64" }
  }
  mock_data "aws_ec2_instance_type" {
    defaults = { supported_architectures = ["x86_64"] }
  }
  mock_resource "aws_db_instance" {
    defaults = { master_user_secret = [{ secret_arn = "arn:aws:secretsmanager:ap-northeast-2:444455556666:secret:master-000001", kms_key_id = "test", secret_status = "active" }] }
  }
  mock_data "aws_caller_identity" {
    defaults = { account_id = "444455556666" }
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
  target          = aws_security_group.client
  values          = { id = "sg-00000000000000001" }
}
override_resource {
  override_during = plan
  target          = aws_security_group.management
  values          = { id = "sg-00000000000000002" }
}

override_resource {
  override_during = plan
  target          = aws_subnet.private["apne2-az1/client"]
  values          = { id = "subnet-00000000000000001" }
}
override_resource {
  override_during = plan
  target          = aws_route_table.private["apne2-az1/client"]
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
  target          = aws_subnet.private["apne2-az2/client"]
  values          = { id = "subnet-00000000000000003" }
}
override_resource {
  override_during = plan
  target          = aws_route_table.private["apne2-az2/client"]
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


override_resource {
  target          = aws_vpc.client
  values          = { id = "vpc-00000000000000001" }
  override_during = plan
}
override_resource {
  target          = aws_subnet.internet_firewall["apne2-az1"]
  values          = { id = "subnet-00000000000000031" }
  override_during = plan
}
override_resource {
  target          = aws_subnet.internet_firewall["apne2-az2"]
  values          = { id = "subnet-00000000000000032" }
  override_during = plan
}
override_resource {
  target          = aws_networkfirewall_vpc_endpoint_association.internet["apne2-az1"]
  values          = { vpc_endpoint_association_status = [{ association_sync_state = [{ availability_zone = "ap-northeast-2a", attachment = [{ subnet_id = "subnet-00000000000000031", endpoint_id = "vpce-00000000000000031", status = "READY", status_message = "" }] }] }] }
  override_during = plan
}
override_resource {
  target          = aws_networkfirewall_vpc_endpoint_association.internet["apne2-az2"]
  values          = { vpc_endpoint_association_status = [{ association_sync_state = [{ availability_zone = "ap-northeast-2b", attachment = [{ subnet_id = "subnet-00000000000000032", endpoint_id = "vpce-00000000000000032", status = "READY", status_message = "" }] }] }] }
  override_during = plan
}
variables {
  application_enabled = true
  application         = { ami_id = "ami-00000000000000001", deployment_mode = "compose" }
  internet_inspection = {
    mode            = "bypass"
    firewall_arn    = "arn:aws:network-firewall:ap-northeast-2:111122223333:firewall/test"
    expected_vpc_id = "vpc-00000000000000001"
    primary_az_ids  = ["apne2-az1", "apne2-az2"]
    subnet_cidrs    = { apne2-az1 = "10.10.30.0/24", apne2-az2 = "10.10.31.0/24" }
  }
}
run "prepare_does_not_associate_igw" {
  command = plan
  assert {
    condition     = length(aws_networkfirewall_vpc_endpoint_association.internet) == 2 && length(aws_route_table_association.internet_ingress) == 0 && length(aws_route.internet_ingress) == 2 && length(aws_route.internet_firewall_egress) == 2
    error_message = "Prepare both AZ endpoints and unbound edge routes without inserting the firewall into live ingress."
  }
  assert {
    condition     = output.internet_inspection.endpoint_ids_by_az_id == { apne2-az1 = "vpce-00000000000000031", apne2-az2 = "vpce-00000000000000032" } && !local.internet_inspection_active
    error_message = "Endpoint mapping must match physical AZ and subnet identity."
  }
}
run "activate_both_az_ingress_routes" {
  command = plan
  variables { internet_inspection = { mode = "enabled", firewall_arn = "arn:aws:network-firewall:ap-northeast-2:111122223333:firewall/test", expected_vpc_id = "vpc-00000000000000001", primary_az_ids = ["apne2-az1", "apne2-az2"], subnet_cidrs = { apne2-az1 = "10.10.30.0/24", apne2-az2 = "10.10.31.0/24" } } }
  assert {
    condition     = length(aws_route_table_association.internet_ingress) == 1 && local.internet_inspection_active && aws_route.internet_ingress["apne2-az1"].vpc_endpoint_id == "vpce-00000000000000031" && aws_route.internet_ingress["apne2-az2"].vpc_endpoint_id == "vpce-00000000000000032"
    error_message = "Both public subnets must use their own AZ endpoints and the existing IGW."
  }
}
run "reject_other_vpc" {
  command = plan
  variables { internet_inspection = { mode = "enabled", firewall_arn = "arn:aws:network-firewall:ap-northeast-2:111122223333:firewall/test", expected_vpc_id = "vpc-00000000000000099", primary_az_ids = ["apne2-az1", "apne2-az2"], subnet_cidrs = { apne2-az1 = "10.10.30.0/24", apne2-az2 = "10.10.31.0/24" } } }
  expect_failures = [terraform_data.internet_inspection_contract]
}
run "reject_subnet_collision" {
  command = plan
  variables { internet_inspection = { mode = "enabled", firewall_arn = "arn:aws:network-firewall:ap-northeast-2:111122223333:firewall/test", expected_vpc_id = "vpc-00000000000000001", primary_az_ids = ["apne2-az1", "apne2-az2"], subnet_cidrs = { apne2-az1 = "10.10.1.0/24", apne2-az2 = "10.10.31.0/24" } } }
  expect_failures = [terraform_data.internet_inspection_contract]
}
run "reject_missing_primary_az" {
  command = plan
  variables { internet_inspection = { mode = "enabled", firewall_arn = "arn:aws:network-firewall:ap-northeast-2:111122223333:firewall/test", expected_vpc_id = "vpc-00000000000000001", primary_az_ids = ["apne2-az1"], subnet_cidrs = { apne2-az1 = "10.10.30.0/24", apne2-az2 = "10.10.31.0/24" } } }
  expect_failures = [terraform_data.internet_inspection_contract]
}
