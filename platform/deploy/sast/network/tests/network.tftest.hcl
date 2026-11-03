mock_provider "aws" {}
override_data {
  target = data.aws_instance.platform
  values = { subnet_id = "subnet-00000000000000001", private_ip = "10.80.1.70", vpc_security_group_ids = ["sg-00000000000000001"] }
}
override_data {
  target = data.aws_instance.tooling
  values = { subnet_id = "subnet-00000000000000002", private_ip = "172.31.25.180", vpc_security_group_ids = ["sg-00000000000000002"] }
}
override_data {
  target = data.aws_route_table.platform
  values = { vpc_id = "vpc-00000000000000001" }
}
override_data {
  target = data.aws_route_table.tooling
  values = { vpc_id = "vpc-00000000000000002" }
}
variables {
  account_id                 = "111122223333"
  platform_instance_id       = "i-00000000000000001"
  tooling_instance_id        = "i-00000000000000002"
  platform_route_table_id    = "rtb-00000000000000001"
  tooling_route_table_id     = "rtb-00000000000000002"
  platform_security_group_id = "sg-00000000000000001"
  tooling_security_group_id  = "sg-00000000000000002"
}
run "only_two_hosts_and_private_https" {
  command = plan
  assert {
    condition     = aws_route.to_sast.destination_cidr_block == "172.31.25.180/32" && aws_route.return_to_platform.destination_cidr_block == "10.80.1.70/32"
    error_message = "VPC 전체나 기본 경로 대신 두 VM /32만 연결해야 합니다."
  }
  assert {
    condition = (
      aws_vpc_security_group_ingress_rule.sast_https.referenced_security_group_id == var.platform_security_group_id &&
      aws_vpc_security_group_ingress_rule.sast_https.cidr_ipv4 == null &&
      aws_vpc_security_group_ingress_rule.sast_https.from_port == 443 &&
      aws_vpc_security_group_ingress_rule.sast_https.to_port == 443
    )
    error_message = "플랫폼 SG의 HTTPS만 허용해야 합니다."
  }
}
run "reject_wrong_instance_group" {
  command = plan
  variables { platform_security_group_id = "sg-00000000000000003" }
  expect_failures = [aws_vpc_peering_connection.sast]
}
run "reject_wrong_route_table" {
  command = plan
  override_data {
    target = data.aws_route_table.tooling
    values = { vpc_id = "vpc-00000000000000003" }
  }
  expect_failures = [aws_vpc_peering_connection.sast]
}

override_data {
  target = data.aws_subnet.platform
  values = { vpc_id = "vpc-00000000000000001" }
}
override_data {
  target = data.aws_subnet.tooling
  values = { vpc_id = "vpc-00000000000000002" }
}
