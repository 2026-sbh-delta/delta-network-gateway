# 동규님 platform/deploy/workloads/terraform/aws 이식. 환경 ID마다 새 사설 EC2 한 대.
# state는 우리 S3 backend(키 delta-cp/release/<environment_id>/vm.tfstate)에 둔다. 값은 init의 -backend-config로만 받는다.
terraform {
  required_version = ">= 1.10, < 2.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
  backend "s3" {}
}

# backend(S3)는 컨트롤러 자신의 역할로, 자원 생성은 대상 계정 delta-prod-deploy 위임으로 분리한다.
provider "aws" {
  region              = var.region
  allowed_account_ids = [var.account_id]
  assume_role {
    role_arn     = var.assume_role_arn
    external_id  = var.external_id
    session_name = "delta-release"
  }
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
