locals {
  prefix = "${var.name}-${var.environment}-staging"
  common_labels = merge({
    team        = "delta"
    project     = "delta-multicloud"
    environment = var.environment
    managed-by  = "terraform"
    component   = "staging"
  }, var.labels)
  registry_prefix        = "${var.region}-docker.pkg.dev/${var.project_id}/${local.prefix}/"
  allowed_image_prefixes = length(var.allowed_image_prefixes) > 0 ? var.allowed_image_prefixes : [local.registry_prefix]
  # The staging tool owns this Cloud Armor priority for the runner's current /32. Fixed CIDRs sit just behind it.
  scanner_rule_priority = 1000
  services = toset(["run.googleapis.com", "artifactregistry.googleapis.com", "compute.googleapis.com", "iam.googleapis.com",
  "sqladmin.googleapis.com", "secretmanager.googleapis.com"])
}

resource "terraform_data" "contract" {
  input = {
    scanner_sources = length(var.scanner_public_cidrs)
  }
  lifecycle {
    precondition {
      condition     = var.dynamic_scanner_access || length(var.scanner_public_cidrs) > 0
      error_message = "Set dynamic_scanner_access or scanner_public_cidrs; otherwise Cloud Armor denies every scanner."
    }
  }
}
