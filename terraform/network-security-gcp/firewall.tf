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
    iap             = { priority = 110, action = "allow", sources = toset(["35.235.240.0/20"]), ports = ["22"] }
    private_inspect = { priority = 200, action = var.inspection_mode == "enabled" ? "apply_security_profile_group" : "allow", sources = var.aws_cidrs, ports = ["3000", "8080"] }
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
      ports       = ["3000", "8080"]
    }
  }
  depends_on = [google_network_security_firewall_endpoint_association.vpc]
}
locals {
  app_ingress = var.application_service_accounts == null ? {} : {
    app_health           = { priority = 90, sources = ["35.191.0.0/16", "130.211.0.0/22"], ports = ["3000"], account = var.application_service_accounts.frontend }
    app_proxy            = { priority = 91, sources = [var.application_subnets.proxy], ports = ["3000"], account = var.application_service_accounts.frontend }
    app_frontend_backend = { priority = 92, sources = [var.subnets.workload], ports = ["8080"], account = var.application_service_accounts.backend }
  }
}
resource "google_compute_network_firewall_policy_rule" "application_ingress" {
  for_each                = local.app_ingress
  firewall_policy         = google_compute_network_firewall_policy.central.name
  priority                = each.value.priority
  rule_name               = each.key
  action                  = "allow"
  direction               = "INGRESS"
  enable_logging          = true
  target_service_accounts = [each.value.account]
  match {
    src_ip_ranges = each.value.sources
    layer4_configs {
      ip_protocol = "tcp"
      ports       = each.value.ports
    }
  }
}
resource "google_compute_network_firewall_policy_rule" "database_allow" {
  count                   = var.application_service_accounts == null ? 0 : 1
  firewall_policy         = google_compute_network_firewall_policy.central.name
  priority                = 400
  rule_name               = "app_database_proxy"
  action                  = "allow"
  direction               = "EGRESS"
  enable_logging          = true
  target_service_accounts = distinct(compact([var.application_service_accounts.backend, var.compose_service_account]))
  match {
    dest_ip_ranges = [var.application_subnets.database]
    layer4_configs {
      ip_protocol = "tcp"
      ports       = ["3307"]
    }
  }
}
resource "google_compute_network_firewall_policy_rule" "database_deny" {
  count           = var.application_service_accounts == null ? 0 : 1
  firewall_policy = google_compute_network_firewall_policy.central.name
  priority        = 410
  rule_name       = "app_database_deny_other"
  action          = "deny"
  direction       = "EGRESS"
  enable_logging  = true
  match {
    dest_ip_ranges = [var.application_subnets.database]
    layer4_configs {
      ip_protocol = "tcp"
      ports       = ["3307", "5432"]
    }
  }
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
