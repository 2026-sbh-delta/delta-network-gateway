output "instance_id" {
  description = "Tooling host instance ID (SSM target)."
  value       = aws_instance.host.id
}

output "egress_ip" {
  description = "Current egress IP (Elastic IP if enabled, otherwise the auto-assigned public IP that changes on stop/start)."
  value       = var.elastic_ip ? aws_eip.host[0].public_ip : aws_instance.host.public_ip
}

output "role_arn" {
  description = "Host IAM role. Use it as the subject when registering AWS federation for the LLM API."
  value       = aws_iam_role.host.arn
}

output "ecr_repository_urls" {
  description = "Image repositories for the sast and dast services."
  value       = { for key, repo in aws_ecr_repository.tool : key => repo.repository_url }
}

output "port_forward_command" {
  description = "Open the DAST workspace on this laptop at http://127.0.0.1:8765."
  value       = "aws ssm start-session --region ${var.region} --target ${aws_instance.host.id} --document-name AWS-StartPortForwardingSession --parameters 'portNumber=[\"8765\"],localPortNumber=[\"8765\"]'"
}
