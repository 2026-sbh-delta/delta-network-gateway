resource "aws_iam_role" "controller" {
  name = "delta-platform-jenkins-controller"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "ec2.amazonaws.com" } }]
  })
}
resource "aws_iam_instance_profile" "controller" {
  name = "delta-platform-jenkins-controller"
  role = aws_iam_role.controller.name
}
resource "aws_iam_role_policy_attachment" "ssm" {
  for_each   = { controller = aws_iam_role.controller.name, agent = aws_iam_role.agent.name }
  role       = each.value
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
resource "aws_iam_role_policy" "runtime" {
  for_each = { controller = aws_iam_role.controller.name, agent = aws_iam_role.agent.name }
  name     = "jenkins-runtime"
  role     = each.value
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
      { Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"], Resource = "arn:aws:ecr:${var.region}:${var.account_id}:repository/delta-platform" },
      { Effect = "Allow", Action = ["ssm:GetParameter"], Resource = "arn:aws:ssm:${var.region}:${var.account_id}:parameter/delta-platform/jenkins/${each.key}" },
      { Effect = "Allow", Action = ["s3:GetObject"], Resource = "arn:aws:s3:::delta-platform-artifacts-${var.account_id}/releases/jenkins/*" }
    ]
  })
}
