mock_provider "aws" {
  override_during = plan
  mock_data "aws_subnet" {
    defaults = {
      id                = "subnet-0123456789abcdef0", vpc_id = "vpc-0123456789abcdef0", cidr_block = "10.80.20.0/24",
      availability_zone = "ap-northeast-2a", map_public_ip_on_launch = false, tags = { Project = "delta-platform" }
    }
  }
  mock_data "aws_security_group" { defaults = { vpc_id = "vpc-0123456789abcdef0", tags = { Project = "delta-platform" } } }
  mock_resource "aws_ebs_volume" { defaults = { id = "vol-0123456789abcdef0" } }
  mock_resource "aws_ecr_repository" { defaults = { arn = "arn:aws:ecr:ap-northeast-2:111122223333:repository/delta-platform-ai" } }
}
variables {
  account_id                 = "111122223333"
  subnet_id                  = "subnet-0123456789abcdef0"
  platform_security_group_id = "sg-0123456789abcdef0"
  ami_id                     = "ami-0123456789abcdef0"
}
run "independent_private_ai" {
  command = plan
  assert {
    condition     = aws_ecr_repository.ai.name == "delta-platform-ai" && aws_iam_role.ai.name == "delta-platform-ai" && output.runtime_parameter == "/delta-platform/ai/runtime"
    error_message = "팀원 AI 자원 이름과 런타임 경로를 재사용하지 않아야 합니다."
  }
  assert {
    condition     = !aws_instance.ai.associate_public_ip_address && aws_instance.ai.private_ip == "10.80.20.10" && aws_instance.ai.metadata_options[0].http_tokens == "required"
    error_message = "AI는 지정된 사설 주소와 IMDSv2만 사용해야 합니다."
  }
  assert {
    condition     = aws_vpc_security_group_ingress_rule.platform.cidr_ipv4 == null && aws_vpc_security_group_ingress_rule.platform.from_port == 443 && aws_vpc_security_group_ingress_rule.platform.to_port == 443
    error_message = "AI ingress는 플랫폼 SG의 HTTPS로 제한해야 합니다."
  }
  assert {
    condition     = aws_ebs_volume.data.encrypted && aws_instance.ai.root_block_device[0].encrypted && !strcontains(aws_iam_role_policy.runtime.policy, "sbh-delta-workload") && !strcontains(aws_iam_role_policy.runtime.policy, "/delta/ai/")
    error_message = "데이터는 암호화하고 팀원 state/비밀값 접근 권한을 추가하지 않아야 합니다."
  }
}
run "reject_shared_subnet" {
  command = plan
  override_data {
    target = data.aws_subnet.ai
    values = { cidr_block = "10.80.1.0/24" }
  }
  expect_failures = [data.aws_subnet.ai]
}
run "reject_large_vm" {
  command = plan
  variables { instance_type = "t4g.2xlarge" }
  expect_failures = [var.instance_type]
}
