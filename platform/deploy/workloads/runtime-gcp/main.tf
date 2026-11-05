terraform {
  required_version = ">= 1.11, < 2.0"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 7.0" }
  }
}
provider "google" {
  project = var.project_id
  region  = "asia-northeast3"
}
data "google_project" "current" {}
data "google_compute_network" "workload" { name = var.network_name }
data "google_compute_subnetwork" "app" {
  name   = var.subnet_name
  region = "asia-northeast3"
}
resource "google_compute_address" "worker_database" {
  name         = "delta-platform-db-worker"
  region       = "asia-northeast3"
  address_type = "INTERNAL"
  address      = var.worker_database_address
  subnetwork   = var.vpn_subnet
}
resource "google_compute_forwarding_rule" "worker_database" {
  name                    = "delta-platform-db-worker"
  region                  = "asia-northeast3"
  network                 = var.vpn_network
  ip_address              = google_compute_address.worker_database.id
  target                  = var.database_service_attachment
  load_balancing_scheme   = ""
  allow_psc_global_access = true
}
resource "google_service_account" "accounts" {
  for_each     = toset(["deployer", "app"])
  account_id   = "delta-platform-${each.key}"
  display_name = "Delta platform ${each.key}"
}
resource "google_iam_workload_identity_pool" "aws" {
  workload_identity_pool_id = "delta-deployment-aws"
  display_name              = "Delta deployment worker"
}
resource "google_iam_workload_identity_pool_provider" "aws" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.aws.workload_identity_pool_id
  workload_identity_pool_provider_id = "aws-ec2"
  attribute_mapping = {
    "google.subject"     = "assertion.arn"
    "attribute.aws_role" = "assertion.arn.extract('assumed-role/{role}/')"
  }
  attribute_condition = "assertion.account == '${var.aws_account_id}' && attribute.aws_role == '${var.aws_deployer_role}'"
  aws { account_id = var.aws_account_id }
}
resource "google_service_account_iam_member" "federation" {
  service_account_id = google_service_account.accounts["deployer"].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.aws.name}/attribute.aws_role/${var.aws_deployer_role}"
}
resource "google_service_account_iam_member" "act_as" {
  service_account_id = google_service_account.accounts["app"].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.accounts["deployer"].email}"
}
resource "google_project_iam_custom_role" "provision" {
  role_id = "deltaPlatformVmProvision"
  title   = "Delta customer VM creation and discovery"
  permissions = [
    "compute.instances.create", "compute.instances.list", "compute.instances.get",
    "compute.disks.create", "compute.disks.get", "compute.images.get", "compute.images.useReadOnly",
    "compute.machineTypes.get", "compute.zones.get", "compute.zoneOperations.get",
    "compute.regionOperations.get", "compute.globalOperations.get", "compute.projects.get",
    "compute.networks.get", "compute.subnetworks.get", "serviceusage.services.use",
  ]
}
resource "google_project_iam_member" "provision" {
  project = var.project_id
  role    = google_project_iam_custom_role.provision.name
  member  = "serviceAccount:${google_service_account.accounts["deployer"].email}"
}
resource "google_project_iam_custom_role" "manage" {
  role_id = "deltaPlatformVmManage"
  title   = "Delta customer VM lifecycle"
  permissions = [
    "compute.instances.delete", "compute.instances.setMetadata", "compute.instances.setLabels",
    "compute.instances.setTags", "compute.instances.setServiceAccount", "compute.instances.setShieldedInstanceIntegrityPolicy",
    "compute.instances.osLogin", "compute.instances.osAdminLogin", "compute.disks.delete", "compute.disks.use",
  ]
}
resource "google_project_iam_member" "manage" {
  project = var.project_id
  role    = google_project_iam_custom_role.manage.name
  member  = "serviceAccount:${google_service_account.accounts["deployer"].email}"
  condition {
    title      = "platform_customer_resources"
    expression = "resource.name.startsWith('projects/${var.project_id}/zones/${var.zone}/instances/delta-app-') || resource.name.startsWith('projects/${var.project_id}/zones/${var.zone}/disks/delta-app-')"
  }
}
resource "google_project_iam_member" "iap" {
  project = var.project_id
  role    = "roles/iap.tunnelResourceAccessor"
  member  = "serviceAccount:${google_service_account.accounts["deployer"].email}"
  condition {
    title      = "ssh_only"
    expression = "destination.port == 22"
  }
}
resource "google_compute_subnetwork_iam_member" "use" {
  subnetwork = data.google_compute_subnetwork.app.name
  region     = "asia-northeast3"
  role       = "roles/compute.networkUser"
  member     = "serviceAccount:${google_service_account.accounts["deployer"].email}"
}
resource "google_artifact_registry_repository" "app" {
  location      = "asia-northeast3"
  repository_id = "delta-platform-apps"
  format        = "DOCKER"
}
resource "google_artifact_registry_repository_iam_member" "access" {
  for_each   = { deployer = "roles/artifactregistry.writer", app = "roles/artifactregistry.reader" }
  location   = "asia-northeast3"
  repository = google_artifact_registry_repository.app.name
  role       = each.value
  member     = "serviceAccount:${google_service_account.accounts[each.key].email}"
}
resource "google_secret_manager_secret_iam_member" "admin" {
  secret_id = var.database_secret
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.accounts["deployer"].email}"
}
resource "google_project_iam_custom_role" "secret_create" {
  role_id     = "deltaPlatformDbSecretCreate"
  title       = "Create customer database credential secrets"
  permissions = ["secretmanager.secrets.create"]
}
resource "google_project_iam_member" "secret_create" {
  project = var.project_id
  role    = google_project_iam_custom_role.secret_create.name
  member  = "serviceAccount:${google_service_account.accounts["deployer"].email}"
}
resource "google_project_iam_custom_role" "secret_manage" {
  role_id     = "deltaPlatformDbSecretManage"
  title       = "Manage platform customer database credential secrets"
  permissions = ["secretmanager.secrets.get", "secretmanager.secrets.getIamPolicy", "secretmanager.secrets.setIamPolicy", "secretmanager.versions.add"]
}
resource "google_project_iam_member" "secret_manage" {
  project = var.project_id
  role    = google_project_iam_custom_role.secret_manage.name
  member  = "serviceAccount:${google_service_account.accounts["deployer"].email}"
  condition {
    title      = "platform_db_secrets"
    expression = "resource.name.startsWith('projects/${data.google_project.current.number}/secrets/delta-db-')"
  }
}
# Classic rules run before the existing network firewall policy. Only the new
# platform app identity is targeted; teammate app identities are unchanged.
resource "google_compute_firewall" "ingress" {
  for_each = {
    iap      = { ranges = ["35.235.240.0/20"], ports = ["22"] }
    deployer = { ranges = [var.deployer_cidr], ports = ["8088"] }
    lb       = { ranges = [var.proxy_cidr, "35.191.0.0/16", "130.211.0.0/22"], ports = ["8088"] }
  }
  name                    = "delta-platform-app-${each.key}"
  network                 = data.google_compute_network.workload.name
  direction               = "INGRESS"
  priority                = 100
  source_ranges           = each.value.ranges
  target_service_accounts = [google_service_account.accounts["app"].email]
  allow {
    protocol = "tcp"
    ports    = each.value.ports
  }
  log_config { metadata = "EXCLUDE_ALL_METADATA" }
}
resource "google_compute_firewall" "database" {
  name                    = "delta-platform-app-database"
  network                 = data.google_compute_network.workload.name
  direction               = "EGRESS"
  priority                = 100
  destination_ranges      = ["${var.database_address}/32"]
  target_service_accounts = [google_service_account.accounts["app"].email]
  allow {
    protocol = "tcp"
    ports    = ["5432"]
  }
  log_config { metadata = "EXCLUDE_ALL_METADATA" }
}
