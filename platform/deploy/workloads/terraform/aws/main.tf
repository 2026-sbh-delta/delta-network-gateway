terraform {
  required_version = ">= 1.10, < 2.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}
provider "aws" {
  region              = "ap-northeast-2"
  allowed_account_ids = [var.account_id]
}
resource "aws_instance" "app" {
  ami                         = var.ami_id
  instance_type               = var.profile_id
  subnet_id                   = var.subnet_id
  vpc_security_group_ids      = var.security_group_ids
  iam_instance_profile        = var.instance_profile
  associate_public_ip_address = false
  user_data                   = <<-SCRIPT
    #!/bin/bash
    set -eu
    dnf install -y docker python3
    systemctl enable --now docker
    # Block customer containers from the instance identity endpoint.
    iptables -I DOCKER-USER -d 169.254.169.254/32 -j REJECT
    cat > /etc/systemd/system/delta-container-isolation.service <<'UNIT'
    [Unit]
    Requires=docker.service
    After=docker.service
    Before=delta-app.service
    [Service]
    Type=oneshot
    RemainAfterExit=yes
    ExecStart=/bin/sh -c 'iptables -C DOCKER-USER -d 169.254.169.254/32 -j REJECT || iptables -I DOCKER-USER -d 169.254.169.254/32 -j REJECT'
    [Install]
    WantedBy=multi-user.target
    UNIT
    systemctl daemon-reload
    systemctl enable delta-container-isolation.service
    touch /var/lib/delta-bootstrap-ready
  SCRIPT
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }
  root_block_device {
    encrypted   = true
    volume_type = "gp3"
    volume_size = 12
  }
  tags = {
    Name        = "delta-app-${var.environment_id}"
    ManagedBy   = "delta-platform"
    ServiceId   = var.service_id
    Environment = var.environment_id
  }
}
