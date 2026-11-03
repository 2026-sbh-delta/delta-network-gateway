locals {
  prefix = "${var.name}-${var.environment}-tooling"
  common_tags = merge({
    Project     = var.name
    Environment = var.environment
    Component   = "security-tooling"
    ManagedBy   = "terraform"
  }, var.tags)
  repositories = toset(["sast", "dast"])
}

data "aws_subnet" "host" {
  id = var.subnet_id
  lifecycle {
    postcondition {
      condition     = self.vpc_id == var.vpc_id
      error_message = "subnet_id must belong to vpc_id."
    }
  }
}

# Amazon Linux 2023 (arm64). SSM Agent is preinstalled.
data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

# ---------- images ----------

resource "aws_ecr_repository" "tool" {
  for_each             = local.repositories
  name                 = "${var.name}-tooling/${each.key}"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false
  image_scanning_configuration {
    scan_on_push = true
  }
  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "tool" {
  for_each   = aws_ecr_repository.tool
  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the latest 10 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 10 }
      action       = { type = "expire" }
    }]
  })
}

# ---------- identity ----------

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "host" {
  name               = "${local.prefix}-host"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.host.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "host" {
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid       = "PullToolImages"
    actions   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"]
    resources = [for repo in aws_ecr_repository.tool : repo.arn]
  }
  statement {
    sid       = "ReadToolParameters"
    actions   = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
    resources = ["arn:aws:ssm:${var.region}:${var.account_id}:parameter${trimsuffix(var.parameter_path, "/")}/*"]
  }
  statement {
    sid       = "DecryptToolParameters"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.region}.amazonaws.com"]
    }
  }
  dynamic "statement" {
    for_each = var.enable_outbound_federation ? [1] : []
    content {
      sid       = "OutboundIdentityFederation"
      actions   = ["sts:GetWebIdentityToken"]
      resources = ["*"]
    }
  }
}

resource "aws_iam_role_policy" "host" {
  name   = "${local.prefix}-host"
  role   = aws_iam_role.host.id
  policy = data.aws_iam_policy_document.host.json
}

resource "aws_iam_instance_profile" "host" {
  name = "${local.prefix}-host"
  role = aws_iam_role.host.name
}

# ---------- network ----------

# No inbound rules at all. Operators reach the workspace through SSM port forwarding.
resource "aws_security_group" "host" {
  name        = "${local.prefix}-host"
  description = "Security tooling host: no inbound, egress for ECR, SSM, LLM API and staging"
  vpc_id      = var.vpc_id
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.host.id
  description       = "Egress for ECR, SSM, LLM API, GitHub and staging ALB"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

# ---------- host ----------

resource "aws_instance" "host" {
  ami                         = data.aws_ssm_parameter.al2023_arm64.value
  instance_type               = var.instance_type
  subnet_id                   = var.subnet_id
  vpc_security_group_ids      = [aws_security_group.host.id]
  iam_instance_profile        = aws_iam_instance_profile.host.name
  associate_public_ip_address = false
  user_data_replace_on_change = true
  user_data                   = file("${path.module}/user_data.sh")

  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = var.root_volume_gb
    encrypted   = true
  }

  lifecycle {
    ignore_changes = [ami]
  }

  tags = { Name = "${local.prefix}-host" }
}

# Fixed egress IP: the staging ALB allows only this /32 for DAST.
resource "aws_eip" "host" {
  domain   = "vpc"
  instance = aws_instance.host.id
  tags     = { Name = "${local.prefix}-host" }
}
