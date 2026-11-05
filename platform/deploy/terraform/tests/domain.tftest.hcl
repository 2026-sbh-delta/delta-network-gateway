mock_provider "aws" {
  override_during = plan
  mock_data "aws_caller_identity" { defaults = { account_id = "111122223333" } }
  mock_data "aws_ec2_managed_prefix_list" { defaults = { id = "pl-12345678" } }
  mock_data "aws_security_group" { defaults = { id = "sg-00000000000000001" } }
}
variables {
  account_id        = "111122223333"
  availability_zone = "ap-northeast-2a"
  ami_id            = "ami-00000000000000001"
}
run "custom_domain_uses_tls_and_canonical_origin" {
  command = plan
  variables {
    custom_domain                 = "delta.example.com"
    custom_domain_certificate_arn = "arn:aws:acm:us-east-1:111122223333:certificate/11111111-2222-3333-4444-555555555555"
  }
  assert {
    condition     = contains(aws_cloudfront_distribution.platform.aliases, "delta.example.com") && !aws_cloudfront_distribution.platform.viewer_certificate[0].cloudfront_default_certificate && aws_cloudfront_distribution.platform.viewer_certificate[0].minimum_protocol_version == "TLSv1.2_2021" && output.platform_url == "https://delta.example.com"
    error_message = "관리 도메인은 일치하는 인증서와 HTTPS origin을 사용해야 합니다."
  }
}
run "domain_without_certificate_is_rejected" {
  command = plan
  variables { custom_domain = "delta.example.com" }
  expect_failures = [aws_cloudfront_distribution.platform]
}
run "certificate_from_another_account_is_rejected" {
  command = plan
  variables {
    custom_domain                 = "delta.example.com"
    custom_domain_certificate_arn = "arn:aws:acm:us-east-1:444455556666:certificate/11111111-2222-3333-4444-555555555555"
  }
  expect_failures = [aws_cloudfront_distribution.platform]
}
run "certificate_in_another_region_is_rejected" {
  command = plan
  variables {
    custom_domain_certificate_arn = "arn:aws:acm:ap-northeast-2:111122223333:certificate/11111111-2222-3333-4444-555555555555"
  }
  expect_failures = [var.custom_domain_certificate_arn]
}
