# 동규님 platform/deploy/workloads/terraform/gcp 이식. 환경 ID마다 새 사설 VM 한 대.
# state는 우리 S3 backend(키 delta-cp/release/<environment_id>/vm.tfstate)에 둔다. 값은 init의 -backend-config로만 받는다.
terraform {
  required_version = ">= 1.10, < 2.0"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 7.0" }
  }
  backend "s3" {}
}
provider "google" {
  project = var.project_id
  region  = var.region
}
resource "google_compute_instance" "app" {
  name         = "delta-app-${var.environment_id}"
  zone         = var.zone
  machine_type = var.profile_id
  boot_disk {
    initialize_params {
      image = var.image
      size  = 12
      type  = "pd-balanced"
    }
  }
  network_interface { subnetwork = var.subnetwork }
  service_account {
    email  = var.service_account
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }
  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }
  metadata = {
    enable-oslogin         = "TRUE"
    block-project-ssh-keys = "TRUE"
  }
  metadata_startup_script = <<-SCRIPT
    #!/bin/bash
    set -eu
    if ${var.preinstalled_runtime}; then
      test -f /etc/delta-runtime-image
      docker compose version
      python3 --version
    else
      apt-get update -qq
      DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io python3
    fi
    systemctl enable --now docker
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
  tags                    = [var.network_tag]
  labels = {
    managed-by  = "delta-platform"
    service-id  = var.service_id
    environment = var.environment_id
  }
}
