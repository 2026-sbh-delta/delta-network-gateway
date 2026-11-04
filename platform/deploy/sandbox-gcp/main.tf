locals {
  name      = "delta-platform-sandbox"
  aws_roles = { runner = var.platform_role_name, scanner = var.scanner_role_name }
}
data "google_project" "current" {}
resource "google_project_service" "apis" {
  for_each           = toset(["run.googleapis.com", "artifactregistry.googleapis.com", "compute.googleapis.com", "iam.googleapis.com", "iamcredentials.googleapis.com", "sts.googleapis.com"])
  service            = each.value
  disable_on_destroy = false
}
resource "google_project_service_identity" "run" {
  provider   = google-beta
  service    = "run.googleapis.com"
  depends_on = [google_project_service.apis]
}
resource "google_compute_network" "sandbox" {
  name                            = local.name
  auto_create_subnetworks         = false
  delete_default_routes_on_create = true
  depends_on                      = [google_project_service.apis]
}
resource "google_compute_subnetwork" "sandbox" {
  name                     = local.name
  network                  = google_compute_network.sandbox.id
  ip_cidr_range            = var.subnet_cidr
  private_ip_google_access = false
}
resource "google_compute_firewall" "deny_egress" {
  name               = "${local.name}-deny-egress"
  network            = google_compute_network.sandbox.id
  direction          = "EGRESS"
  priority           = 100
  destination_ranges = ["0.0.0.0/0"]
  deny { protocol = "all" }
  log_config { metadata = "EXCLUDE_ALL_METADATA" }
}
resource "google_service_account" "accounts" {
  for_each     = toset(["runtime", "runner", "scanner"])
  account_id   = "delta-sandbox-${each.key}"
  display_name = "Delta sandbox ${each.key}"
  depends_on   = [google_project_service.apis]
}
resource "google_artifact_registry_repository" "sandbox" {
  location               = var.region
  repository_id          = local.name
  format                 = "DOCKER"
  cleanup_policy_dry_run = false
  cleanup_policies {
    id     = "expire-images"
    action = "DELETE"
    condition {
      tag_state  = "ANY"
      older_than = "86400s"
    }
  }
  depends_on = [google_project_service.apis]
}
resource "google_artifact_registry_repository_iam_member" "writer" {
  location   = var.region
  repository = google_artifact_registry_repository.sandbox.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.accounts["runner"].email}"
}
resource "google_project_iam_custom_role" "runner" {
  role_id     = "deltaSandboxRunner"
  title       = "Delta sandbox lifecycle"
  permissions = ["run.services.create", "run.services.get", "run.services.delete", "run.services.getIamPolicy", "run.services.setIamPolicy", "run.revisions.get", "run.operations.get"]
}
resource "google_project_iam_custom_role" "scanner" {
  role_id     = "deltaSandboxScanner"
  title       = "Delta sandbox identity reader"
  permissions = ["run.services.get", "run.revisions.get"]
}
resource "google_project_iam_member" "runner" {
  project = var.project_id
  role    = google_project_iam_custom_role.runner.name
  member  = "serviceAccount:${google_service_account.accounts["runner"].email}"
}
resource "google_project_iam_member" "scanner" {
  project = var.project_id
  role    = google_project_iam_custom_role.scanner.name
  member  = "serviceAccount:${google_service_account.accounts["scanner"].email}"

}
resource "google_service_account_iam_member" "act_as_runtime" {
  service_account_id = google_service_account.accounts["runtime"].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.accounts["runner"].email}"
}
resource "google_service_account_iam_member" "id_token" {
  for_each           = local.aws_roles
  service_account_id = google_service_account.accounts[each.key].name
  role               = "roles/iam.serviceAccountOpenIdTokenCreator"
  member             = "serviceAccount:${google_service_account.accounts[each.key].email}"
}
resource "google_compute_subnetwork_iam_member" "run_network" {
  subnetwork = google_compute_subnetwork.sandbox.name
  role       = "roles/compute.networkUser"
  member     = "serviceAccount:${google_project_service_identity.run.email}"
}
resource "google_iam_workload_identity_pool" "aws" {
  workload_identity_pool_id = "delta-sandbox-aws"
  display_name              = "Delta AWS sandbox workers"
  depends_on                = [google_project_service.apis]
}
resource "google_iam_workload_identity_pool_provider" "aws" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.aws.workload_identity_pool_id
  workload_identity_pool_provider_id = "aws-ec2"
  attribute_mapping = {
    "google.subject"     = "assertion.arn"
    "attribute.aws_role" = "assertion.arn.extract('assumed-role/{role}/')"
  }
  attribute_condition = "assertion.account == '${var.aws_account_id}' && attribute.aws_role in ['${var.platform_role_name}', '${var.scanner_role_name}']"
  aws { account_id = var.aws_account_id }
}
resource "google_service_account_iam_member" "federation" {
  for_each           = local.aws_roles
  service_account_id = google_service_account.accounts[each.key].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.aws.name}/attribute.aws_role/${each.value}"
}
resource "aws_iam_role_policy" "copy" {
  name = "gcp-sandbox-image-read"
  role = var.platform_role_name
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"], Resource = "arn:aws:ecr:ap-northeast-2:${var.aws_account_id}:repository/delta-platform-builds" }
  ] })
}
