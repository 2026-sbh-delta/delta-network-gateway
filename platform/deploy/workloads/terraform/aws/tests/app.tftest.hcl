mock_provider "aws" {}
variables {
  account_id         = "111122223333"
  ami_id             = "ami-0123456789abcdef0"
  subnet_id          = "subnet-0123456789abcdef0"
  security_group_ids = ["sg-0123456789abcdef0"]
  instance_profile   = "delta-app-pull-ssm"
  environment_id     = "00000000-0000-0000-0000-000000000001"
  service_id         = "00000000-0000-0000-0000-000000000002"
  profile_id         = "t3.micro"
}
run "private_pinned_vm" {
  command = plan
  assert {
    condition     = !aws_instance.app.associate_public_ip_address && aws_instance.app.metadata_options[0].http_tokens == "required"
    error_message = "VM은 공인 IP 없이 IMDSv2를 사용해야 합니다."
  }
  assert {
    condition     = aws_instance.app.root_block_device[0].encrypted && aws_instance.app.instance_type == var.profile_id
    error_message = "암호화 디스크와 선택 사양이 필요합니다."
  }
}
