mock_provider "aws" {
  override_during = plan
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  mock_data "aws_security_group" {
    defaults = { id = "sg-00000000000000009" }
  }
  mock_data "aws_ssm_parameter" {
    defaults = { value = "ami-00000000000000001" }
  }
}

variables {
  account_id = "444455556666"
}

run "network_and_edge_are_closed" {
  command = plan

  assert {
    condition     = aws_lb.internal.internal && aws_lb.internal.drop_invalid_header_fields
    error_message = "ALB는 내부용이어야 하고 잘못된 헤더를 버려야 한다."
  }
  assert {
    condition     = aws_vpc_security_group_ingress_rule.alb_from_cloudfront.cidr_ipv4 == null
    error_message = "ALB는 CloudFront VPC Origin 보안 그룹에서만 받아야 한다(대역 허용 금지)."
  }
  assert {
    condition     = length(aws_route_table.alb.route) == 0
    error_message = "ALB 서브넷에는 인터넷 경로가 없어야 한다."
  }
  assert {
    condition     = alltrue([for subnet in aws_subnet.public : subnet.map_public_ip_on_launch == false])
    error_message = "퍼블릭 서브넷도 공인 IP를 자동으로 주면 안 된다."
  }
  assert {
    condition     = aws_cloudfront_distribution.app["dast"].default_cache_behavior[0].viewer_protocol_policy == "redirect-to-https"
    error_message = "공개 앱은 HTTPS로만 받아야 한다."
  }
  assert {
    condition     = one(aws_cloudfront_distribution.app["dast"].origin).custom_header == toset([{ name = "X-Delta-App", value = "dast" }])
    error_message = "배포마다 앱 헤더를 붙여 ALB가 대상을 고르게 해야 한다."
  }
  assert {
    condition     = aws_iam_role.llm.name == "delta-cp-llm"
    error_message = "LLM을 쓰는 서비스가 공유하는 신원 역할이 있어야 한다."
  }
  assert {
    condition     = length(aws_default_security_group.main.ingress) == 0 && length(aws_default_security_group.main.egress) == 0
    error_message = "기본 보안 그룹에는 규칙이 없어야 한다."
  }
}

run "service_host_is_private" {
  command = plan
  module {
    source = "./modules/service-host"
  }
  variables {
    name              = "delta-cp-test"
    service           = "test"
    region            = "ap-northeast-2"
    security_group_id = "sg-00000000000000001"
    subnet_id         = "subnet-00000000000000001"
    ami_id            = "ami-00000000000000001"
    instance_type     = "t4g.small"
    parameter_paths   = ["/delta-cp/test/"]
    user_data         = "#!/bin/bash"
  }
  assert {
    condition     = aws_instance.this.associate_public_ip_address == false
    error_message = "서비스 인스턴스에 공인 IP가 있으면 안 된다."
  }
  assert {
    condition     = aws_instance.this.metadata_options[0].http_tokens == "required" && aws_instance.this.metadata_options[0].http_put_response_hop_limit == 1
    error_message = "IMDSv2 강제, 홉 제한 1이어야 한다."
  }
  assert {
    condition     = aws_instance.this.root_block_device[0].encrypted
    error_message = "디스크는 암호화해야 한다."
  }
  assert {
    condition     = aws_ecr_repository.this.image_tag_mutability == "IMMUTABLE"
    error_message = "이미지 태그는 덮어쓸 수 없어야 한다."
  }
  assert {
    condition     = length(aws_iam_role_policy.extra) == 0
    error_message = "추가 권한을 주지 않으면 추가 정책도 없어야 한다."
  }
}
