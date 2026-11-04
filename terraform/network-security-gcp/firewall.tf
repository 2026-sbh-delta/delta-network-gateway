resource "google_network_security_security_profile" "threat" {
  name     = "${var.name}-threat"
  parent   = local.project_parent
  location = "global"
  type     = "THREAT_PREVENTION"
  threat_prevention_profile {
    dynamic "severity_overrides" {
      for_each = toset(["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFORMATIONAL"])
      content {
        severity = severity_overrides.key
        action   = "ALERT"
      }
    }
  }
  depends_on = [google_project_service.required]
}
resource "google_network_security_security_profile_group" "threat" {
  name                      = "${var.name}-threat"
  parent                    = local.project_parent
  location                  = "global"
  threat_prevention_profile = google_network_security_security_profile.threat.id
}
resource "google_network_security_firewall_endpoint" "central" {
  count    = var.inspection_mode == "disabled" ? 0 : 1
  name     = "${var.name}-central"
  parent   = local.project_parent
  location = var.zone
  # Omit the default false setting: the API returns no block for it, which
  # otherwise creates a ForceNew difference on the next plan. VPC MTU is 1460.
  depends_on = [google_project_service.required]
}
resource "google_network_security_firewall_endpoint_association" "vpc" {
  for_each          = var.inspection_mode == "disabled" ? {} : local.subnet_map
  name              = "${var.name}-${each.key}"
  parent            = local.project_parent
  location          = var.zone
  network           = google_compute_network.vpc[each.key].id
  firewall_endpoint = google_network_security_firewall_endpoint.central[0].id
  disabled          = false
}
resource "google_compute_network_firewall_policy" "central" {
  name       = "${var.name}-central"
  depends_on = [google_project_service.required]
}
resource "google_compute_network_firewall_policy_association" "vpc" {
  for_each          = local.subnet_map
  name              = "${var.name}-${each.key}"
  attachment_target = google_compute_network.vpc[each.key].id
  firewall_policy   = google_compute_network_firewall_policy.central.name
}
locals {
  ingress_rules = {
    health           = { priority = 100, action = "allow", sources = toset(["35.191.0.0/16", "209.85.204.0/22"]), ports = ["8080"] }
    iap              = { priority = 110, action = "allow", sources = toset(["35.235.240.0/20"]), ports = ["22"] }
    private_inspect  = { priority = 200, action = var.inspection_mode == "enabled" ? "apply_security_profile_group" : "allow", sources = var.aws_cidrs, ports = ["8080", "8081"] }
    internet_inspect = { priority = 210, action = var.inspection_mode == "enabled" ? "apply_security_profile_group" : "allow", sources = var.internet_source_cidrs, ports = ["8080"] }
    internet_deny    = { priority = 220, action = "deny", sources = var.internet_source_cidrs, ports = ["8081"] }
  }
}
resource "google_compute_network_firewall_policy_rule" "ingress" {
  for_each               = local.ingress_rules
  firewall_policy        = google_compute_network_firewall_policy.central.name
  priority               = each.value.priority
  rule_name              = each.key
  action                 = each.value.action
  direction              = "INGRESS"
  enable_logging         = true
  security_profile_group = each.value.action == "apply_security_profile_group" ? "//networksecurity.googleapis.com/${google_network_security_security_profile_group.threat.id}" : null
  match {
    src_ip_ranges = sort(tolist(each.value.sources))
    layer4_configs {
      ip_protocol = "tcp"
      ports       = each.value.ports
    }
  }
  depends_on = [google_network_security_firewall_endpoint_association.vpc]
}
resource "google_compute_network_firewall_policy_rule" "private_egress" {
  firewall_policy        = google_compute_network_firewall_policy.central.name
  priority               = 300
  action                 = var.inspection_mode == "enabled" ? "apply_security_profile_group" : "allow"
  direction              = "EGRESS"
  enable_logging         = true
  security_profile_group = var.inspection_mode == "enabled" ? "//networksecurity.googleapis.com/${google_network_security_security_profile_group.threat.id}" : null
  match {
    dest_ip_ranges = sort(tolist(var.aws_cidrs))
    layer4_configs {
      ip_protocol = "tcp"
      ports       = ["8080", "8081"]
    }
  }
  depends_on = [google_network_security_firewall_endpoint_association.vpc]
}
resource "google_compute_network_firewall_policy_rule" "deny_ingress" {
  firewall_policy = google_compute_network_firewall_policy.central.name
  priority        = 1000
  action          = "deny"
  direction       = "INGRESS"
  enable_logging  = true
  match {
    src_ip_ranges = ["0.0.0.0/0"]
    layer4_configs { ip_protocol = "all" }
  }
}
