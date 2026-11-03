# New rule resources have request tags, not existing resource tags.
# The parent security group must still satisfy MaintainTaggedEC2.
resource "aws_iam_role_policy" "apply_rule_creation" {
  name = "create-project-security-group-rules"
  role = aws_iam_role.github["apply"].name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ec2:AuthorizeSecurityGroupIngress", "ec2:AuthorizeSecurityGroupEgress"]
      Resource = "${local.ec2}:security-group-rule/*"
      Condition = { StringEquals = {
        "aws:RequestTag/Project" = var.name
        "aws:RequestedRegion"    = var.region
      } }
    }]
  })
}
