output "role_arns" {
  value = { for k, r in aws_iam_role.this : k => r.arn }
}

output "external_id" {
  value = var.external_id
}
