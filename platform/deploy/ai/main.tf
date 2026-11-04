locals {
  name      = "delta-platform-ai"
  parameter = "/delta-platform/ai/runtime"
}
data "aws_subnet" "ai" {
  id = var.subnet_id
  lifecycle {
    postcondition {
      condition     = self.cidr_block == "10.80.20.0/24" && !self.map_public_ip_on_launch && self.tags["Project"] == "delta-platform"
      error_message = "플랫폼 Terraform이 관리하는 AI 전용 사설 서브넷이 필요합니다."
    }
  }
}
data "aws_security_group" "platform" {
  id = var.platform_security_group_id
  lifecycle {
    postcondition {
      condition     = self.vpc_id == data.aws_subnet.ai.vpc_id && self.tags["Project"] == "delta-platform"
      error_message = "같은 플랫폼 VPC의 API/Worker SG가 필요합니다."
    }
  }
}
resource "aws_ecr_repository" "ai" {
  name                 = local.name
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false
  encryption_configuration { encryption_type = "AES256" }
  image_scanning_configuration { scan_on_push = true }
}
resource "aws_ecr_lifecycle_policy" "ai" {
  repository = aws_ecr_repository.ai.name
  policy = jsonencode({ rules = [{ rulePriority = 1, description = "Keep latest ten AI images",
    selection = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 10 }, action = { type = "expire" }
  }] })
}
resource "aws_iam_role" "ai" {
  name = local.name
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "ec2.amazonaws.com" }
  }] })
}
resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.ai.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
resource "aws_iam_role_policy" "runtime" {
  name = "platform-ai-runtime"
  role = aws_iam_role.ai.name
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = "ecr:GetAuthorizationToken", Resource = "*" },
    { Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"], Resource = aws_ecr_repository.ai.arn },
    { Effect = "Allow", Action = "ssm:GetParameter", Resource = "arn:aws:ssm:${var.region}:${var.account_id}:parameter${local.parameter}" },
    { Effect = "Allow", Action = "s3:GetObject", Resource = "arn:aws:s3:::delta-platform-artifacts-${var.account_id}/releases/ai/*" }
  ] })
}
resource "aws_iam_instance_profile" "ai" {
  name = local.name
  role = aws_iam_role.ai.name
}
resource "aws_security_group" "ai" {
  name        = local.name
  description = "Platform-only AI HTTPS API; no public or SSH ingress"
  vpc_id      = data.aws_subnet.ai.vpc_id
}
resource "aws_vpc_security_group_ingress_rule" "platform" {
  security_group_id            = aws_security_group.ai.id
  referenced_security_group_id = data.aws_security_group.platform.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}
resource "aws_vpc_security_group_egress_rule" "https" {
  security_group_id = aws_security_group.ai.id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}
resource "aws_ebs_volume" "data" {
  availability_zone = data.aws_subnet.ai.availability_zone
  size              = 20
  type              = "gp3"
  encrypted         = true
  tags              = { Name = "${local.name}-data" }
  lifecycle { prevent_destroy = true }
}
resource "aws_instance" "ai" {
  ami                         = var.ami_id
  instance_type               = var.instance_type
  subnet_id                   = data.aws_subnet.ai.id
  private_ip                  = "10.80.20.10"
  associate_public_ip_address = false
  vpc_security_group_ids      = [aws_security_group.ai.id]
  iam_instance_profile        = aws_iam_instance_profile.ai.name
  user_data = templatefile("${path.module}/bootstrap.sh.tftpl", {
    volume_serial = replace(aws_ebs_volume.data.id, "-", "")
  })
  user_data_replace_on_change = true
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }
  credit_specification { cpu_credits = "standard" }
  root_block_device {
    volume_type = "gp3"
    volume_size = 16
    encrypted   = true
  }
  tags       = { Name = local.name }
  depends_on = [aws_iam_role_policy.runtime, aws_iam_role_policy_attachment.ssm, aws_vpc_security_group_egress_rule.https]
}
resource "aws_volume_attachment" "data" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.data.id
  instance_id = aws_instance.ai.id
}
