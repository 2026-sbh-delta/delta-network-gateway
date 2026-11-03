output "platform_role_name" {
  value       = var.platform_role_name
  description = "관측 조회 정책을 연결한 기존 역할"
}
output "policy_name" {
  value       = aws_iam_role_policy.observability_read.name
  description = "플랫폼의 관측 읽기 전용 정책"
}
