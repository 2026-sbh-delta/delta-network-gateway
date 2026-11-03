output "role_arns" {
  value       = { for key, role in aws_iam_role.github : key => role.arn }
  description = "GitHub repository variables에 입력할 세 역할 ARN"
}
output "oidc_provider_arn" {
  value       = local.oidc_arn
  description = "생성 또는 재사용한 GitHub OIDC provider"
}
output "trusted_subject" {
  value       = local.subject
  description = "허용한 GitHub main subject"
}
