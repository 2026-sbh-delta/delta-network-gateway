output "platform_url" {
  value       = "https://${var.custom_domain == null ? aws_cloudfront_distribution.platform.domain_name : var.custom_domain}"
  description = "팀 접속용 HTTPS 주소"
}
output "cloudfront_url" {
  value       = "https://${aws_cloudfront_distribution.platform.domain_name}"
  description = "DNS CNAME 대상 확인과 복구용 기존 CloudFront 주소"
}
output "instance_id" {
  value       = aws_instance.app.id
  description = "플랫폼 VM SSM 배포 대상"
}
output "nat_instance_id" {
  value       = aws_instance.nat.id
  description = "NAT VM 진단 대상"
}
output "ecr_repository_url" {
  value       = aws_ecr_repository.platform.repository_url
  description = "플랫폼 이미지 저장소"
}
output "artifact_bucket" {
  value       = aws_s3_bucket.backup.id
  description = "릴리스 파일·DB 백업의 비공개 버킷"
}
output "data_volume_id" {
  value       = aws_ebs_volume.data.id
  description = "삭제 보호한 DB 디스크"
}
output "runtime_parameter" {
  value       = "/${var.name}/runtime"
  description = "배포 도구가 별도로 채울 SSM SecureString 경로"
}
output "distribution_id" {
  value       = aws_cloudfront_distribution.platform.id
  description = "CloudFront 배포 ID"
}
output "region" {
  value       = var.region
  description = "AWS 리전"
}
output "account_id" {
  value       = var.account_id
  description = "허용한 AWS 계정"
}
output "jenkins_instances" {
  value       = { for name, instance in aws_instance.jenkins : name => instance.id }
  description = "Jenkins controller/agent의 SSM 배포 대상 VM ID"
}
output "jenkins_url" {
  value       = "https://${aws_instance.jenkins["controller"].private_ip}"
  description = "플랫폼과 전용 에이전트에서만 접근하는 사설 HTTPS 주소"
}

output "service_subnets" {
  description = "역할별 사설 서브넷과 경로 테이블"
  value = { for name, subnet in aws_subnet.service : name => {
    id = subnet.id, cidr = subnet.cidr_block, route_table_id = aws_route_table.service[name].id
  } }
}
