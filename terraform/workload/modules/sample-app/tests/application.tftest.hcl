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
}
variables {
  prefix                       = "delta-lab-notes"
  region                       = "ap-northeast-2"
  account_id                   = "444455556666"
  vpc_id                       = "vpc-00000000000000001"
  vpc_cidr                     = "10.10.0.0/16"
  existing_subnet_cidrs        = ["10.10.0.0/24", "10.10.1.0/24", "10.10.2.0/24", "10.10.3.0/24"]
  management_security_group_id = "sg-00000000000000001"
  management_endpoint_id       = "eice-00000000000000001"
  instance_az_id               = "apne2-az1"
  workload_subnets = {
    apne2-az1 = { id = "subnet-00000000000000001", route_table_id = "rtb-00000000000000001", az_name = "ap-northeast-2a" }
    apne2-az2 = { id = "subnet-00000000000000002", route_table_id = "rtb-00000000000000002", az_name = "ap-northeast-2b" }
  }
  config = { ami_id = "ami-00000000000000001" }
}
override_resource {
  override_during = plan
  target          = aws_security_group.app["frontend"]
  values          = { id = "sg-00000000000000002" }
}
override_resource {
  override_during = plan
  target          = aws_security_group.app["backend"]
  values          = { id = "sg-00000000000000003" }
}
run "private_database_public_alb_and_retained_routes" {
  command = plan
  assert {
    condition     = !aws_lb.app.internal && length(aws_subnet.public) == 2 && length(aws_subnet.database) == 2 && aws_route.app_egress.route_table_id == "rtb-00000000000000001" && aws_route.app_egress.destination_cidr_block == "0.0.0.0/0" && length(aws_route_table.database) == 2
    error_message = "The app adds a two-AZ ALB and isolated DB subnets; only the selected existing workload RT gets NAT egress."
  }
  assert {
    condition     = aws_vpc_security_group_ingress_rule.service["backend"].referenced_security_group_id == aws_security_group.app["frontend"].id && aws_vpc_security_group_ingress_rule.service["database"].referenced_security_group_id == aws_security_group.app["backend"].id && aws_vpc_security_group_ingress_rule.service["frontend"].from_port == 3000 && aws_vpc_security_group_ingress_rule.service["backend"].from_port == 8080 && aws_vpc_security_group_ingress_rule.service["database"].from_port == 5432
    error_message = "Backend and database ingress must be restricted to the preceding service SG."
  }
  assert {
    condition     = aws_db_instance.app.manage_master_user_password && aws_db_instance.app.deletion_protection && !aws_db_instance.app.skip_final_snapshot && aws_db_instance.app.backup_retention_period == 7 && alltrue([for instance in aws_instance.app : instance.metadata_options[0].http_tokens == "required" && instance.root_block_device[0].encrypted && !instance.associate_public_ip_address])
    error_message = "Database credentials/backups and private EC2 metadata/storage protections are required."
  }
}
run "reject_existing_subnet_collision" {
  command = plan
  variables { config = { ami_id = "ami-00000000000000001", public_subnet_cidrs = { apne2-az1 = "10.10.1.0/24", apne2-az2 = "10.10.11.0/24" } } }
  expect_failures = [terraform_data.contract]
}
run "reject_subnet_outside_vpc" {
  command = plan
  variables { config = { ami_id = "ami-00000000000000001", db_subnet_cidrs = { apne2-az1 = "10.20.20.0/24", apne2-az2 = "10.10.21.0/24" } } }
  expect_failures = [terraform_data.contract]
}
run "reject_arm_ami" {
  command = plan
  override_data {
    target = data.aws_ami.selected
    values = { architecture = "arm64" }
  }
  expect_failures = [terraform_data.contract]
}
run "reject_arm_instance_type" {
  command = plan
  override_data {
    target = data.aws_ec2_instance_type.selected
    values = { supported_architectures = ["arm64"] }
  }
  expect_failures = [terraform_data.contract]
}
