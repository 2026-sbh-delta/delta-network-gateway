data "aws_iam_instance_profile" "jenkins_controller" { name = "${var.name}-jenkins-controller" }
data "aws_iam_instance_profile" "jenkins_agent" { name = "${var.name}-jenkins-agent" }

resource "aws_security_group" "jenkins_controller" {
  name_prefix = "${var.name}-jenkins-controller-"
  description = "Private HTTPS from platform and dedicated builder only"
  vpc_id      = aws_vpc.platform.id
}
resource "aws_security_group" "jenkins_agent" {
  name_prefix = "${var.name}-jenkins-agent-"
  description = "Dedicated builder with no inbound access"
  vpc_id      = aws_vpc.platform.id
}
resource "aws_vpc_security_group_ingress_rule" "jenkins_platform" {
  security_group_id            = aws_security_group.jenkins_controller.id
  referenced_security_group_id = aws_security_group.app.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}
resource "aws_vpc_security_group_ingress_rule" "jenkins_agent" {
  security_group_id            = aws_security_group.jenkins_controller.id
  referenced_security_group_id = aws_security_group.jenkins_agent.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}
resource "aws_vpc_security_group_egress_rule" "jenkins_https" {
  for_each          = { controller = aws_security_group.jenkins_controller.id, agent = aws_security_group.jenkins_agent.id }
  security_group_id = each.value
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}
resource "aws_ebs_volume" "jenkins" {
  availability_zone = var.availability_zone
  size              = 20
  type              = "gp3"
  encrypted         = true
  tags              = { Name = "${var.name}-jenkins-home" }
  lifecycle { prevent_destroy = true }
}
resource "aws_instance" "jenkins" {
  for_each = {
    controller = { ip = "10.80.10.20", type = "t3.small", profile = data.aws_iam_instance_profile.jenkins_controller.name, sg = aws_security_group.jenkins_controller.id, disk = 12 }
    agent      = { ip = "10.80.11.21", type = "t3.medium", profile = data.aws_iam_instance_profile.jenkins_agent.name, sg = aws_security_group.jenkins_agent.id, disk = 40 }
  }
  ami                         = var.jenkins_ami_id
  instance_type               = each.value.type
  subnet_id                   = aws_subnet.service["jenkins_${each.key}"].id
  private_ip                  = each.value.ip
  associate_public_ip_address = false
  vpc_security_group_ids      = [each.value.sg]
  iam_instance_profile        = each.value.profile
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = each.key == "agent" ? 2 : 1
  }
  credit_specification { cpu_credits = "standard" }
  root_block_device {
    volume_size = each.value.disk
    volume_type = "gp3"
    encrypted   = true
  }
  user_data = templatefile("${path.module}/templates/jenkins.sh.tftpl", {
    volume_serial = each.key == "controller" ? replace(aws_ebs_volume.jenkins.id, "-", "") : ""
  })
  user_data_replace_on_change = true
  tags                        = { Name = "${var.name}-jenkins-${each.key}" }
  depends_on                  = [aws_route.nat, aws_eip.nat, aws_vpc_security_group_egress_rule.jenkins_https, aws_route.service_nat, aws_vpc_security_group_ingress_rule.service_nat]
}
resource "aws_volume_attachment" "jenkins" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.jenkins.id
  instance_id = aws_instance.jenkins["controller"].id
}
