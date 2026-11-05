# 서비스 하나 = EC2 하나. 역할·보안 그룹·이미지 저장소·파라미터 경로를 서비스마다 따로 둔다.

locals {
  readable_parameters = concat(
    [for p in var.parameter_paths : "arn:aws:ssm:${var.region}:${var.account_id}:parameter${trimsuffix(p, "/")}/*"],
    [for n in var.extra_parameter_names : "arn:aws:ssm:${var.region}:${var.account_id}:parameter${n}"],
  )
}

resource "aws_ecr_repository" "this" {
  name                 = var.name
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false
  image_scanning_configuration {
    scan_on_push = true
  }
  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "this" {
  repository = aws_ecr_repository.this.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the latest 10 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 10 }
      action       = { type = "expire" }
    }]
  })
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "this" {
  name               = var.name
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.this.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "this" {
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid       = "PullOwnImage"
    actions   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"]
    resources = [aws_ecr_repository.this.arn]
  }
  statement {
    sid       = "ReadOwnParameters"
    actions   = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
    resources = local.readable_parameters
  }
  # AmazonSSMManagedInstanceCore는 모든 파라미터의 GetParameter(s)를 허용한다. 위의 경로 제한이 실제로 효과가 있도록
  # 자기 경로·지정 파라미터 밖은 명시적으로 거부한다(2026-11-05 실측: 이 거부 없이는 모든 인스턴스가 모든 비밀값을 읽었다).
  statement {
    sid           = "DenyOtherParameters"
    effect        = "Deny"
    actions       = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath", "ssm:GetParameterHistory"]
    not_resources = local.readable_parameters
  }
  statement {
    sid       = "DecryptViaSsm"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.region}.amazonaws.com"]
    }
  }
  dynamic "statement" {
    for_each = var.llm_role_arn == null ? [] : [1]
    content {
      sid       = "AssumeLlmIdentity"
      actions   = ["sts:AssumeRole"]
      resources = [var.llm_role_arn]
    }
  }
}

resource "aws_iam_role_policy" "this" {
  name   = var.name
  role   = aws_iam_role.this.id
  policy = data.aws_iam_policy_document.this.json
}

resource "aws_iam_role_policy" "extra" {
  count  = var.extra_policy_json == null ? 0 : 1
  name   = "${var.name}-extra"
  role   = aws_iam_role.this.id
  policy = var.extra_policy_json
}

resource "aws_iam_instance_profile" "this" {
  name = var.name
  role = aws_iam_role.this.name
}

resource "aws_instance" "this" {
  ami                         = var.ami_id
  instance_type               = var.instance_type
  subnet_id                   = var.subnet_id
  vpc_security_group_ids      = [var.security_group_id]
  iam_instance_profile        = aws_iam_instance_profile.this.name
  associate_public_ip_address = false
  user_data_replace_on_change = true
  user_data                   = var.user_data

  dynamic "instance_market_options" {
    for_each = var.spot ? [1] : []
    content {
      market_type = "spot"
      spot_options {
        spot_instance_type             = "persistent"
        instance_interruption_behavior = "stop"
      }
    }
  }

  # 컨테이너는 호스트 네트워크로 돈다. 홉 제한 1이면 브리지 컨테이너는 인스턴스 역할에 닿지 못한다.
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
    http_endpoint               = "enabled"
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = var.root_volume_gb
    encrypted   = true
  }

  lifecycle {
    ignore_changes = [ami]
  }

  tags = { Name = var.name, Service = var.service }
}
