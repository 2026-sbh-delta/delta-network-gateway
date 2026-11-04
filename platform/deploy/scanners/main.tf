locals {
  scanners = {
    sast = { cidr = "10.80.30.0/24", ip = "10.80.30.10", type = "t4g.small", disk = 20 }
    dast = { cidr = "10.80.40.0/24", ip = "10.80.40.10", type = "t4g.medium", disk = 30 }
  }
}
data "aws_subnet" "scanner" {
  for_each = local.scanners
  id       = var.subnet_ids[each.key]
  lifecycle {
    postcondition {
      condition     = self.cidr_block == each.value.cidr && !self.map_public_ip_on_launch && self.tags["Project"] == "delta-platform"
      error_message = "플랫폼이 배정한 검사기 전용 사설 subnet이 필요합니다."
    }
  }
}
data "aws_security_group" "platform" {
  id = var.platform_security_group_id
  lifecycle {
    postcondition {
      condition     = alltrue([for s in data.aws_subnet.scanner : s.vpc_id == self.vpc_id]) && self.tags["Project"] == "delta-platform"
      error_message = "같은 플랫폼 VPC의 API/Worker SG여야 합니다."
    }
  }
}
data "aws_iam_instance_profile" "tooling" { name = var.tooling_instance_profile }
data "aws_lb" "sandbox" {
  name = "delta-platform-sandbox"
  lifecycle {
    postcondition {
      condition     = self.internal && self.vpc_id == data.aws_subnet.scanner["dast"].vpc_id && self.tags["Project"] == "delta-platform"
      error_message = "플랫폼 전용 내부 샌드박스 ALB만 검사 대상으로 허용합니다."
    }
  }
}
resource "aws_security_group" "scanner" {
  for_each    = local.scanners
  name        = "delta-platform-${each.key}"
  vpc_id      = data.aws_subnet.scanner[each.key].vpc_id
  description = "Private ${each.key} API; platform-managed dedicated scanner"
}
resource "aws_vpc_security_group_ingress_rule" "platform" {
  for_each                     = local.scanners
  security_group_id            = aws_security_group.scanner[each.key].id
  referenced_security_group_id = data.aws_security_group.platform.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}
resource "aws_vpc_security_group_ingress_rule" "dast_sast" {
  security_group_id            = aws_security_group.scanner["sast"].id
  referenced_security_group_id = aws_security_group.scanner["dast"].id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}
resource "aws_vpc_security_group_egress_rule" "https" {
  for_each          = local.scanners
  security_group_id = aws_security_group.scanner[each.key].id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}
resource "aws_vpc_security_group_egress_rule" "dast_sandbox" {
  security_group_id            = aws_security_group.scanner["dast"].id
  referenced_security_group_id = one(data.aws_lb.sandbox.security_groups)
  ip_protocol                  = "tcp"
  from_port                    = 80
  to_port                      = 80
}
resource "aws_vpc_security_group_ingress_rule" "sandbox" {
  security_group_id            = one(data.aws_lb.sandbox.security_groups)
  referenced_security_group_id = aws_security_group.scanner["dast"].id
  ip_protocol                  = "tcp"
  from_port                    = 80
  to_port                      = 80
}
resource "aws_ebs_volume" "data" {
  for_each          = local.scanners
  availability_zone = data.aws_subnet.scanner[each.key].availability_zone
  size              = 20
  type              = "gp3"
  encrypted         = true
  tags              = { Name = "delta-platform-${each.key}-data" }
  lifecycle { prevent_destroy = true }
}
resource "aws_instance" "scanner" {
  for_each                    = local.scanners
  ami                         = var.ami_id
  instance_type               = each.value.type
  subnet_id                   = data.aws_subnet.scanner[each.key].id
  private_ip                  = each.value.ip
  associate_public_ip_address = false
  vpc_security_group_ids      = [aws_security_group.scanner[each.key].id]
  iam_instance_profile        = data.aws_iam_instance_profile.tooling.name
  user_data = templatefile("${path.module}/bootstrap.sh.tftpl", {
    volume_serial = replace(aws_ebs_volume.data[each.key].id, "-", "")
  })
  user_data_replace_on_change = true
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }
  credit_specification { cpu_credits = "standard" }
  root_block_device {
    volume_size = each.value.disk
    volume_type = "gp3"
    encrypted   = true
  }
  tags       = { Name = "delta-platform-${each.key}" }
  depends_on = [aws_vpc_security_group_egress_rule.https]
}
resource "aws_volume_attachment" "data" {
  for_each    = local.scanners
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.data[each.key].id
  instance_id = aws_instance.scanner[each.key].id
}
