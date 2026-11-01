resource "aws_security_group" "server" {
  name_prefix = "${local.prefix}-"
  description = "Private test server; SSH only from its management endpoint"
  vpc_id      = aws_vpc.server.id
  tags        = { Name = local.prefix }
}

resource "aws_security_group" "management" {
  name_prefix = "${local.prefix}-eice-"
  description = "Management endpoint; SSH only to the private test server"
  vpc_id      = aws_vpc.server.id
  tags        = { Name = "${local.prefix}-eice" }
}

resource "aws_vpc_security_group_ingress_rule" "ssh" {
  security_group_id            = aws_security_group.server.id
  referenced_security_group_id = aws_security_group.management.id
  ip_protocol                  = "tcp"
  from_port                    = 22
  to_port                      = 22
  description                  = "IAM-authorized EIC Endpoint SSH only"
}

resource "aws_vpc_security_group_egress_rule" "management" {
  security_group_id            = aws_security_group.management.id
  referenced_security_group_id = aws_security_group.server.id
  ip_protocol                  = "tcp"
  from_port                    = 22
  to_port                      = 22
  description                  = "SSH to the test server only"
}

resource "aws_vpc_security_group_ingress_rule" "test" {
  for_each          = local.ingress
  security_group_id = aws_security_group.server.id
  cidr_ipv4         = each.value.cidr
  ip_protocol       = "tcp"
  from_port         = each.value.port
  to_port           = each.value.port
  description       = "Client ANF test, including blocked-port control"
}

resource "aws_instance" "server" {
  ami                         = data.aws_ami.server.id
  private_ip                  = var.server_private_ip
  user_data                   = templatefile("${path.module}/templates/startup.sh.tftpl", { app_base64 = filebase64("${path.module}/app/server.py") })
  user_data_replace_on_change = true
  instance_type               = "t4g.nano"
  subnet_id                   = aws_subnet.private["${var.server_az_id}/server"].id
  vpc_security_group_ids      = [aws_security_group.server.id]
  associate_public_ip_address = false
  ipv6_address_count          = 0
  monitoring                  = false
  credit_specification {
    cpu_credits = "standard"
  }
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
    instance_metadata_tags      = "disabled"
  }
  root_block_device {
    volume_type           = "gp3"
    volume_size           = 8
    encrypted             = true
    delete_on_termination = true
  }
  tags       = { Name = local.prefix }
  depends_on = [aws_route_table_association.private, aws_vpc_security_group_ingress_rule.ssh]
}

resource "aws_ec2_instance_connect_endpoint" "management" {
  subnet_id          = aws_subnet.private["${var.server_az_id}/server"].id
  security_group_ids = [aws_security_group.management.id]
  preserve_client_ip = false
  ip_address_type    = "ipv4"
  tags               = { Name = "${local.prefix}-eice" }
  depends_on         = [aws_vpc_security_group_egress_rule.management]
}
