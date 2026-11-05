resource "google_compute_global_address" "staging" {
  name       = "${local.prefix}-ip"
  depends_on = [google_project_service.enabled]
}

resource "google_compute_region_network_endpoint_group" "app" {
  name                  = "${local.prefix}-neg"
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = google_cloud_run_v2_service.app.name
  }
}

# Deny everyone by default. The staging tool adds, patches and removes the scanner /32 rule at
# local.scanner_rule_priority, so Terraform ignores inline rule drift and keeps fixed CIDRs in a separate rule.
resource "google_compute_security_policy" "scanner" {
  name        = "${local.prefix}-scanner"
  description = "Only DAST scanner sources reach Delta staging"
  rule {
    action      = "deny(403)"
    priority    = 2147483647
    description = "Deny everyone else"
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
  }
  lifecycle {
    ignore_changes = [rule]
  }
  depends_on = [google_project_service.enabled]
}

resource "google_compute_security_policy_rule" "fixed_scanners" {
  count           = length(var.scanner_public_cidrs) > 0 ? 1 : 0
  security_policy = google_compute_security_policy.scanner.name
  priority        = local.scanner_rule_priority + 1
  action          = "allow"
  description     = "Fixed DAST runner egress"
  match {
    versioned_expr = "SRC_IPS_V1"
    config {
      src_ip_ranges = sort(tolist(var.scanner_public_cidrs))
    }
  }
}

resource "google_compute_backend_service" "app" {
  name                  = "${local.prefix}-backend"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  security_policy       = google_compute_security_policy.scanner.id
  backend {
    group = google_compute_region_network_endpoint_group.app.id
  }
  log_config {
    enable      = true
    sample_rate = 1
  }
}

resource "google_compute_url_map" "app" {
  name            = "${local.prefix}-lb"
  default_service = google_compute_backend_service.app.id
}

resource "google_compute_target_http_proxy" "app" {
  name    = "${local.prefix}-http"
  url_map = google_compute_url_map.app.id
}

resource "google_compute_global_forwarding_rule" "http" {
  name                  = "${local.prefix}-http"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  ip_address            = google_compute_global_address.staging.address
  port_range            = "80"
  target                = google_compute_target_http_proxy.app.id
}
