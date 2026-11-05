output "app_urls" {
  description = "CloudFront로 공개한 앱 주소."
  value       = { for key, dist in aws_cloudfront_distribution.app : key => "https://${dist.domain_name}" }
}

output "nat_egress_ip" {
  description = "플랫폼이 밖으로 나가는 고정 IP. 스테이징 접근 허용 목록에 넣는다."
  value       = aws_eip.nat.public_ip
}

output "instance_ids" {
  description = "서비스별 인스턴스 ID(SSM 대상)."
  value       = { for key, host in module.host : key => host.instance_id }
}

output "ecr_repository_urls" {
  description = "서비스별 이미지 저장소."
  value       = { for key, host in module.host : key => host.repository_url }
}

output "role_arns" {
  description = "서비스별 인스턴스 역할."
  value       = { for key, host in module.host : key => host.role_arn }
}

output "llm_role_arn" {
  description = "공용 LLM 신원 역할. Anthropic OIDC 규칙에 이 ARN 하나만 등록한다."
  value       = aws_iam_role.llm.arn
}

output "internal_names" {
  description = "서비스 사설 이름."
  value       = { for key, record in aws_route53_record.service : key => record.fqdn }
}

output "parameter_prefix" {
  description = "Parameter Store 경로 접두사."
  value       = "/${local.prefix}/"
}

output "region" {
  value = var.region
}

output "links" {
  description = "열어 둔 서비스 사이 연결(연결 확인 스크립트가 읽는다)."
  value       = local.links
}

output "service_ports" {
  description = "서비스별로 받는 포트(연결 확인 스크립트가 기대 목록을 만든다). Jenkins는 스테이징 도구 8091을 함께 연다."
  value = { for k, s in local.services : k => (
    s.port == 0 ? [] : distinct(concat([s.port], [for l in local.links : l.port if l.to == k && l.port != s.port]))
  ) }
}
