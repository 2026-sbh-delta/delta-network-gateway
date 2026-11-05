resource "aws_iam_role_policy" "observability_read" {
  name = "delta-platform-observability-read"
  role = var.platform_role_name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["cloudwatch:GetMetricData", "cloudwatch:ListMetrics", "ec2:DescribeInstances", "xray:GetTraceSummaries", "xray:BatchGetTraces"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["logs:GetLogEvents"]
        Resource = "arn:aws:logs:${var.region}:${var.account_id}:log-group:/delta-platform/apps/*:log-stream:application"
      }
    ]
  })
}
