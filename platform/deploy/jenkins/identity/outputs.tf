output "agent_role_arn" {
  value       = aws_iam_role.agent.arn
  description = "전용 빌드 에이전트 역할"
}
output "agent_instance_profile" {
  value       = aws_iam_instance_profile.agent.name
  description = "후속 에이전트 EC2에 연결할 instance profile"
}
output "repository_url" {
  value       = data.aws_ecr_repository.customer.repository_url
  description = "기존 고객 이미지 ECR URI"
}
