locals {
  oidc_arn      = "arn:aws:iam::${var.account_id}:oidc-provider/token.actions.githubusercontent.com"
  subject       = "${var.github_subject_prefix}:ref:refs/heads/main"
  ec2           = "arn:aws:ec2:${var.region}:${var.account_id}"
  iam           = "arn:aws:iam::${var.account_id}"
  cloudfront    = "arn:aws:cloudfront::${var.account_id}"
  bucket        = "arn:aws:s3:::${var.name}-artifacts-${var.account_id}"
  state         = "arn:aws:s3:::${var.state_bucket}"
  state_object  = "${local.state}/platform/terraform.tfstate"
  lock_object   = "${local.state_object}.tflock"
  ecr           = "arn:aws:ecr:${var.region}:${var.account_id}:repository/${var.name}"
  project       = { StringEquals = { "ec2:ResourceTag/Project" = var.name, "aws:RequestedRegion" = var.region } }
  cloud_project = { StringEquals = { "aws:ResourceTag/Project" = var.name } }
  roles         = toset(["plan", "apply", "release"])
  vm_roles      = [for suffix in ["app", "nat", "jenkins-controller", "jenkins-agent"] : "${local.iam}:role/${var.name}-${suffix}"]
  vm_profiles   = [for suffix in ["app", "nat", "jenkins-controller", "jenkins-agent"] : "${local.iam}:instance-profile/${var.name}-${suffix}"]
  trust = {
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow", Action = "sts:AssumeRoleWithWebIdentity"
      Principal = { Federated = local.oidc_arn }
      Condition = { StringEquals = {
        "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        "token.actions.githubusercontent.com:sub" = local.subject
      } }
    }]
  }
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.create_oidc_provider ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  lifecycle { prevent_destroy = true }
}
data "aws_iam_openid_connect_provider" "existing" {
  count = var.create_oidc_provider ? 0 : 1
  arn   = local.oidc_arn
}
resource "aws_iam_role" "github" {
  for_each             = local.roles
  name                 = "${var.name}-github-${each.key}"
  assume_role_policy   = jsonencode(local.trust)
  max_session_duration = 3600
  depends_on           = [aws_iam_openid_connect_provider.github]
  lifecycle {
    precondition {
      condition     = var.create_oidc_provider ? true : contains(data.aws_iam_openid_connect_provider.existing[0].client_id_list, "sts.amazonaws.com")
      error_message = "기존 OIDC provider에 sts.amazonaws.com audience가 필요합니다."
    }
  }
}
resource "aws_iam_policy" "plan" {
  name   = "${var.name}-github-plan"
  policy = jsonencode(local.plan_policy)
}
resource "aws_iam_policy" "apply" {
  name   = "${var.name}-github-apply"
  policy = jsonencode(local.apply_policy)
}
resource "aws_iam_policy" "release" {
  name   = "${var.name}-github-release"
  policy = jsonencode(local.release_policy)
}
resource "aws_iam_role_policy_attachment" "read" {
  for_each   = toset(["plan", "apply"])
  role       = aws_iam_role.github[each.key].name
  policy_arn = aws_iam_policy.plan.arn
}
resource "aws_iam_role_policy_attachment" "apply" {
  role       = aws_iam_role.github["apply"].name
  policy_arn = aws_iam_policy.apply.arn
}
resource "aws_iam_role_policy_attachment" "release" {
  role       = aws_iam_role.github["release"].name
  policy_arn = aws_iam_policy.release.arn
}
