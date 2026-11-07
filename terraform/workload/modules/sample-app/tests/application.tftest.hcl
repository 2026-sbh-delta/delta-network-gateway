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

run "compose_retains_backend_during_cutover" {
  command = plan
  variables {
    config = { ami_id = "ami-00000000000000001", deployment_mode = "compose", retain_legacy_backend = true }
  }
  assert {
    condition     = length(aws_instance.app) == 2 && output.deployment.service_hosts.backend == "frontend" && contains(keys(aws_vpc_security_group_ingress_rule.service), "database") && contains(keys(aws_vpc_security_group_ingress_rule.service), "compose_database")
    error_message = "Cutover must keep the original backend and DB path while preparing the Compose host."
  }
}

run "compose_single_host_keeps_database_and_log_history" {
  command = plan
  variables {
    config = { ami_id = "ami-00000000000000001", deployment_mode = "compose" }
  }
  assert {
    condition     = keys(aws_instance.app) == ["frontend"] && keys(aws_iam_role.app) == ["frontend"] && length(aws_cloudwatch_log_group.app) == 2 && length(aws_security_group.app) == 3 && keys(aws_vpc_security_group_ingress_rule.service) == ["compose_database", "frontend"]
    error_message = "The final stack must have one host/role, no inter-host backend port and retain both log groups."
  }
  assert {
    condition     = aws_vpc_security_group_ingress_rule.service["compose_database"].referenced_security_group_id == aws_security_group.app["frontend"].id && aws_vpc_security_group_ingress_rule.service["compose_database"].from_port == 5432 && aws_instance.app["frontend"].user_data_replace_on_change && aws_db_instance.app.deletion_protection && output.deployment.service_hosts.frontend == "frontend" && output.deployment.service_hosts.backend == "frontend"
    error_message = "Compose needs the host DB path, explicit role mapping and the existing DB/EC2 protection contract."
  }
}

run "reject_undefined_runtime_mode" {
  command = plan
  variables { config = { deployment_mode = "other" } }
  expect_failures = [var.config]
}

run "reject_native_legacy_migration_flag" {
  command = plan
  variables { config = { retain_legacy_backend = true } }
  expect_failures = [var.config]
}

run "internet_endpoints_keep_public_alb_nat_and_local_flows" {
  command = plan
  override_resource {
    target          = aws_nat_gateway.app
    values          = { id = "nat-00000000000000001" }
    override_during = plan
  }
  override_resource {
    target          = aws_security_group.app["alb"]
    values          = { id = "sg-00000000000000004" }
    override_during = plan
  }
  variables { internet_firewall_endpoints = { apne2-az1 = "vpce-00000000000000031", apne2-az2 = "vpce-00000000000000032" } }
  assert {
    condition     = aws_route.public["apne2-az1"].vpc_endpoint_id == "vpce-00000000000000031" && aws_route.public["apne2-az2"].vpc_endpoint_id == "vpce-00000000000000032" && aws_route.app_egress.nat_gateway_id == aws_nat_gateway.app.id && !aws_lb.app.internal && aws_vpc_security_group_ingress_rule.service["frontend"].referenced_security_group_id == aws_security_group.app["alb"].id
    error_message = "Keep ALB/NAT and local SG flows while public defaults use same-AZ shared endpoints."
  }
}
run "remote_service_is_explicit_and_bidirectional" {
  command = plan
  variables { remote_service_cidrs = ["10.32.1.20/32"] }
  assert {
    condition     = length(aws_vpc_security_group_ingress_rule.remote_service) == 1 && length(aws_vpc_security_group_egress_rule.remote_service) == 1 && aws_vpc_security_group_ingress_rule.remote_service["10.32.1.20/32/3000"].from_port == 3000 && aws_vpc_security_group_ingress_rule.remote_service["10.32.1.20/32/3000"].cidr_ipv4 == "10.32.1.20/32"
    error_message = "Remote service access must be confined to an explicit peer and service port."
  }
}
run "reject_remote_world_access" {
  command = plan
  variables { remote_service_cidrs = ["0.0.0.0/0"] }
  expect_failures = [var.remote_service_cidrs]
}
