data "aws_ecr_repository" "customer" { name = var.repository_name }

resource "aws_iam_role" "agent" {
  name = "delta-platform-jenkins-agent"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "ec2.amazonaws.com" } }],
      var.verification_principal_arn == null ? [] : [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { AWS = var.verification_principal_arn } }]
    )
  })
}
resource "aws_iam_role_policy" "publish" {
  name = "publish-customer-images"
  role = aws_iam_role.agent.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
      { Effect = "Allow", Action = ["ecr:DescribeImages", "ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage"], Resource = data.aws_ecr_repository.customer.arn }
    ]
  })
}
resource "aws_iam_instance_profile" "agent" {
  name = "delta-platform-jenkins-agent"
  role = aws_iam_role.agent.name
}
