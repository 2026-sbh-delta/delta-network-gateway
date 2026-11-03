mock_provider "aws" {}
variables {
  account_id                 = "111122223333"
  platform_instance_id       = "i-00000000000000001"
  tooling_instance_id        = "i-00000000000000002"
  platform_route_table_id    = "rtb-00000000000000001"
  tooling_route_table_id     = "rtb-00000000000000002"
  platform_security_group_id = "sg-00000000000000001"
  tooling_security_group_id  = "sg-00000000000000002"
  dast_sandbox = {
    alb_arn = "arn:aws:elasticloadbalancing:ap-northeast-2:111122223333:loadbalancer/app/delta-platform-sandbox/123"
    subnet_route_tables = {
      "subnet-00000000000000001" = "rtb-00000000000000001"
      "subnet-00000000000000003" = "rtb-00000000000000003"
    }
  }
}
override_data {
  target = data.aws_instance.platform
  values = { subnet_id = "subnet-00000000000000001", private_ip = "10.80.1.70", vpc_security_group_ids = ["sg-00000000000000001"] }
}
override_data {
  target = data.aws_instance.tooling
  values = { subnet_id = "subnet-00000000000000002", private_ip = "172.31.25.180", vpc_security_group_ids = ["sg-00000000000000002"] }
}
override_data {
  target = data.aws_subnet.platform
  values = { vpc_id = "vpc-00000000000000001" }
}
override_data {
  target = data.aws_subnet.tooling
  values = { vpc_id = "vpc-00000000000000002" }
}
override_data {
  target = data.aws_route_table.platform
  values = { vpc_id = "vpc-00000000000000001" }
}
override_data {
  target = data.aws_route_table.tooling
  values = { vpc_id = "vpc-00000000000000002" }
}
override_data {
  target = data.aws_lb.dast[0]
  values = {
    internal = true, load_balancer_type = "application", vpc_id = "vpc-00000000000000001",
    name     = "delta-platform-sandbox", tags = { ManagedBy = "terraform" }, security_groups = ["sg-00000000000000003"],
    subnets  = ["subnet-00000000000000001", "subnet-00000000000000003"]
  }
}
override_data {
  target = data.aws_subnet.dast["subnet-00000000000000001"]
  values = { vpc_id = "vpc-00000000000000001", cidr_block = "10.80.1.0/24" }
}
override_data {
  target = data.aws_subnet.dast["subnet-00000000000000003"]
  values = { vpc_id = "vpc-00000000000000001", cidr_block = "10.80.2.0/24" }
}
override_data {
  target = data.aws_route_table.dast["subnet-00000000000000001"]
  values = { vpc_id = "vpc-00000000000000001", associations = [{ subnet_id = "subnet-00000000000000001" }] }
}
override_data {
  target = data.aws_route_table.dast["subnet-00000000000000003"]
  values = { vpc_id = "vpc-00000000000000001", associations = [{ subnet_id = "subnet-00000000000000003" }] }
}
run "private_scanner_reuses_peering_and_never_opens_tasks" {
  command = plan
  assert {
    condition = (
      length(aws_route.dast_to_alb) == 2 && length(aws_route.dast_return) == 1 &&
      aws_vpc_security_group_ingress_rule.dast_http[0].referenced_security_group_id == var.tooling_security_group_id &&
      aws_vpc_security_group_ingress_rule.dast_http[0].cidr_ipv4 == null &&
      aws_vpc_security_group_ingress_rule.dast_http[0].from_port == 80 &&
      aws_vpc_security_group_ingress_rule.dast_http[0].to_port == 80
    )
    error_message = "기존 경로 중복 없이 ALB의 HTTP만 도구 SG에 허용해야 합니다."
  }
}
run "reject_public_alb" {
  command = plan
  override_data {
    target = data.aws_lb.dast[0]
    values = {
      internal = false, load_balancer_type = "application", vpc_id = "vpc-00000000000000001",
      name     = "delta-platform-sandbox", tags = { ManagedBy = "terraform" }, security_groups = ["sg-00000000000000003"],
      subnets  = ["subnet-00000000000000001", "subnet-00000000000000003"]
    }
  }
  expect_failures = [aws_vpc_security_group_ingress_rule.dast_http]
}
run "reject_wrong_subnet_route_table" {
  command = plan
  override_data {
    target = data.aws_route_table.dast["subnet-00000000000000003"]
    values = { vpc_id = "vpc-00000000000000001", associations = [{ subnet_id = "subnet-00000000000000099" }] }
  }
  expect_failures = [aws_route.dast_to_alb]
}
