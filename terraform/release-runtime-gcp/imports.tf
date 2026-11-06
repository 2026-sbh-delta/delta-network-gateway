# 11/6 기존 리소스를 이 루트로 가져온다. 첫 apply 뒤에도 남겨 두면 다시 만들 때 같은 이름을 다시 가져온다.
locals {
  p              = "projects/${var.project_id}"
  deployer_email = "delta-platform-deployer@${var.project_id}.iam.gserviceaccount.com"
  app_email      = "delta-platform-app@${var.project_id}.iam.gserviceaccount.com"
}

import {
  to = google_service_account.deployer
  id = "${local.p}/serviceAccounts/${local.deployer_email}"
}
import {
  to = google_service_account.app
  id = "${local.p}/serviceAccounts/${local.app_email}"
}
import {
  to = google_service_account_iam_member.act_as
  id = "${local.p}/serviceAccounts/${local.app_email} roles/iam.serviceAccountUser serviceAccount:${local.deployer_email}"
}
import {
  to = google_project_iam_custom_role.provision
  id = "${local.p}/roles/deltaPlatformVmProvision"
}
import {
  to = google_project_iam_member.provision
  id = "${var.project_id} ${local.p}/roles/deltaPlatformVmProvision serviceAccount:${local.deployer_email}"
}
import {
  to = google_project_iam_custom_role.manage
  id = "${local.p}/roles/deltaPlatformVmManage"
}
import {
  to = google_project_iam_member.manage
  id = "${var.project_id} ${local.p}/roles/deltaPlatformVmManage serviceAccount:${local.deployer_email} platform_customer_resources"
}
import {
  to = google_project_iam_member.iap
  id = "${var.project_id} roles/iap.tunnelResourceAccessor serviceAccount:${local.deployer_email} ssh_only"
}
import {
  to = google_compute_subnetwork_iam_member.use
  id = "${local.p}/regions/${var.region}/subnetworks/${var.subnet_name} roles/compute.networkUser serviceAccount:${local.deployer_email}"
}
import {
  to = google_project_iam_custom_role.secret_create
  id = "${local.p}/roles/deltaPlatformDbSecretCreate"
}
import {
  to = google_project_iam_member.secret_create
  id = "${var.project_id} ${local.p}/roles/deltaPlatformDbSecretCreate serviceAccount:${local.deployer_email}"
}
import {
  to = google_project_iam_custom_role.secret_manage
  id = "${local.p}/roles/deltaPlatformDbSecretManage"
}
import {
  to = google_project_iam_member.secret_manage
  id = "${var.project_id} ${local.p}/roles/deltaPlatformDbSecretManage serviceAccount:${local.deployer_email} platform_db_secrets"
}
import {
  to = google_artifact_registry_repository.app
  id = "${local.p}/locations/${var.region}/repositories/delta-platform-apps"
}
import {
  to = google_artifact_registry_repository_iam_member.access["deployer"]
  id = "${local.p}/locations/${var.region}/repositories/delta-platform-apps roles/artifactregistry.writer serviceAccount:${local.deployer_email}"
}
import {
  to = google_artifact_registry_repository_iam_member.access["app"]
  id = "${local.p}/locations/${var.region}/repositories/delta-platform-apps roles/artifactregistry.reader serviceAccount:${local.app_email}"
}
import {
  for_each = toset(["iap", "deployer", "lb"])
  to       = google_compute_firewall.ingress[each.key]
  id       = "${local.p}/global/firewalls/delta-platform-app-${each.key}"
}
import {
  to = google_compute_firewall.database
  id = "${local.p}/global/firewalls/delta-platform-app-database"
}
import {
  to = google_compute_firewall.https
  id = "${local.p}/global/firewalls/delta-platform-app-https"
}
import {
  to = google_compute_firewall.egress_deny
  id = "${local.p}/global/firewalls/delta-platform-app-egress-deny"
}
import {
  to = google_compute_route.egress
  id = "${local.p}/global/routes/delta-platform-app-egress"
}
import {
  to = google_compute_router.egress
  id = "${local.p}/regions/${var.region}/routers/delta-platform-app-egress"
}
import {
  to = google_compute_router_nat.egress
  id = "${var.project_id}/${var.region}/delta-platform-app-egress/delta-platform-app-egress"
}

# onboarding-gcp 에서 옮겨 온 운영 배포 권한·방화벽(11/6).
import {
  for_each = toset(["roles/cloudsql.admin", "roles/dns.admin", "roles/compute.networkAdmin", "roles/secretmanager.admin"])
  to       = google_project_iam_member.release_deployer[each.key]
  id       = "${var.project_id} ${each.key} serviceAccount:${local.deployer_email}"
}
import {
  to = google_artifact_registry_repository_iam_member.release_app_pull
  id = "${local.p}/locations/${var.region}/repositories/${var.build_repository} roles/artifactregistry.reader serviceAccount:${local.app_email}"
}
import {
  to = google_project_iam_member.release_app_logs
  id = "${var.project_id} roles/logging.logWriter serviceAccount:${local.app_email}"
}
import {
  to = google_compute_firewall.release_app_database
  id = "${local.p}/global/firewalls/delta-cp-release-app-database"
}
