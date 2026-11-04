mock_provider "aws" {
  override_during = plan
  mock_data "aws_security_group" { defaults = { vpc_id = "vpc-0123456789abcdef0", tags = { Project = "delta-platform" } } }
  mock_data "aws_lb" { defaults = { internal = true, vpc_id = "vpc-0123456789abcdef0", tags = { Project = "delta-platform" }, security_groups = ["sg-11111111111111111"] } }
  mock_resource "aws_ebs_volume" { defaults = { id = "vol-0123456789abcdef0" } }
}
variables {
  account_id                 = "111122223333"
  subnet_ids                 = { sast = "subnet-11111111111111111", dast = "subnet-22222222222222222" }
  platform_security_group_id = "sg-0123456789abcdef0"
  ami_id                     = "ami-0123456789abcdef0"
}
override_data {
  target = data.aws_subnet.scanner["sast"]
  values = { cidr_block = "10.80.30.0/24", vpc_id = "vpc-0123456789abcdef0", availability_zone = "ap-northeast-2a", map_public_ip_on_launch = false, tags = { Project = "delta-platform" } }
}
override_data {
  target = data.aws_subnet.scanner["dast"]
  values = { cidr_block = "10.80.40.0/24", vpc_id = "vpc-0123456789abcdef0", availability_zone = "ap-northeast-2a", map_public_ip_on_launch = false, tags = { Project = "delta-platform" } }
}
run "isolated_scanner_boundaries" {
  command = plan
  assert {
    condition     = alltrue([for vm in aws_instance.scanner : !vm.associate_public_ip_address && vm.metadata_options[0].http_tokens == "required" && vm.root_block_device[0].encrypted])
    error_message = "검사기는 사설 VM·IMDSv2·암호화 디스크를 사용해야 합니다."
  }
  assert {
    condition     = aws_vpc_security_group_ingress_rule.dast_sast.from_port == 443 && aws_vpc_security_group_ingress_rule.dast_sast.cidr_ipv4 == null && aws_vpc_security_group_egress_rule.dast_sandbox.to_port == 80 && aws_vpc_security_group_egress_rule.dast_sandbox.cidr_ipv4 == null
    error_message = "DAST→SAST와 샌드박스 연결은 대상 SG와 포트로 제한해야 합니다."
  }
  assert {
    condition     = aws_instance.scanner["sast"].instance_type == "t4g.small" && aws_instance.scanner["dast"].instance_type == "t4g.medium" && alltrue([for disk in aws_ebs_volume.data : disk.encrypted])
    error_message = "소형 검사 VM과 암호화 기록 디스크를 유지해야 합니다."
  }
}
run "reject_public_sandbox" {
  command = plan
  override_data {
    target = data.aws_lb.sandbox
    values = { internal = false }
  }
  expect_failures = [data.aws_lb.sandbox]
}
