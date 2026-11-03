mock_provider "aws" {
  override_during = plan
  mock_data "aws_caller_identity" { defaults = { account_id = "111122223333" } }
  mock_data "aws_ec2_managed_prefix_list" { defaults = { id = "pl-12345678" } }
  mock_data "aws_security_group" { defaults = { id = "sg-00000000000000001" } }
  mock_resource "aws_instance" { defaults = { arn = "arn:aws:ec2:ap-northeast-2:111122223333:instance/i-00000000000000001", private_dns = "ip-10-80-1-10.ap-northeast-2.compute.internal" } }
  mock_resource "aws_s3_bucket" { defaults = { arn = "arn:aws:s3:::delta-platform-artifacts-111122223333" } }
  mock_resource "aws_ecr_repository" { defaults = { arn = "arn:aws:ecr:ap-northeast-2:111122223333:repository/delta-platform" } }
  mock_resource "aws_ebs_volume" { defaults = { id = "vol-00000000000000001" } }
}
variables {
  account_id        = "111122223333"
  availability_zone = "ap-northeast-2a"
  ami_id            = "ami-00000000000000001"
}
run "private_platform_and_no_auth_cache" {
  command = plan
  assert {
    condition     = !aws_instance.app.associate_public_ip_address && !aws_subnet.private.map_public_ip_on_launch && aws_instance.app.metadata_options[0].http_tokens == "required" && aws_instance.app.metadata_options[0].http_put_response_hop_limit == 1
    error_message = "플랫폼은 사설 VM이며 IMDSv2와 단일 hop을 사용해야 합니다."
  }
  assert {
    condition     = aws_ebs_volume.data.encrypted && aws_instance.app.root_block_device[0].encrypted && aws_ebs_volume.data.size == 20
    error_message = "DB와 루트 디스크는 암호화해야 합니다."
  }
  assert {
    condition     = aws_cloudfront_distribution.platform.default_cache_behavior[0].cache_policy_id == "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" && aws_cloudfront_distribution.platform.default_cache_behavior[0].viewer_protocol_policy == "https-only"
    error_message = "인증 응답을 캐시하지 않고 HTTPS만 허용해야 합니다."
  }
  assert {
    condition     = contains(aws_cloudfront_origin_request_policy.platform.headers_config[0].headers[0].items, "X-CSRF-Token") && contains(aws_cloudfront_origin_request_policy.platform.cookies_config[0].cookies[0].items, "delta_session")
    error_message = "세션과 CSRF 헤더를 API까지 전달해야 합니다."
  }
  assert {
    condition     = length(aws_vpc_security_group_ingress_rule.bootstrap) == 0 && aws_vpc_security_group_ingress_rule.cloudfront.from_port == 8088 && aws_vpc_security_group_ingress_rule.cloudfront.to_port == 8088
    error_message = "최종 수신은 CloudFront 서비스 SG의 앱 포트만 허용해야 합니다."
  }
  assert {
    condition     = !aws_instance.nat.source_dest_check && aws_instance.nat.instance_type == "t4g.micro" && aws_vpc_security_group_ingress_rule.nat_https.cidr_ipv4 == "10.80.1.0/24"
    error_message = "NAT는 플랫폼 사설 서브넷의 HTTPS 송신만 받아야 합니다."
  }
  assert {
    condition     = aws_s3_bucket_public_access_block.backup.block_public_acls && aws_s3_bucket_public_access_block.backup.block_public_policy && aws_s3_bucket_versioning.backup.versioning_configuration[0].status == "Enabled" && !aws_s3_bucket.backup.force_destroy
    error_message = "백업은 비공개·버전 관리·자동 삭제 보호가 필요합니다."
  }
}
run "bootstrap_prefix_is_explicit" {
  command = plan
  variables { bootstrap_origin_access = true }
  assert {
    condition     = length(aws_vpc_security_group_ingress_rule.bootstrap) == 1 && aws_vpc_security_group_ingress_rule.bootstrap[0].prefix_list_id == "pl-12345678"
    error_message = "초기 접근은 CloudFront 관리 prefix list로만 제한해야 합니다."
  }
}
run "reject_large_compute" {
  command = plan
  variables { instance_type = "t4g.2xlarge" }
  expect_failures = [var.instance_type]
}
