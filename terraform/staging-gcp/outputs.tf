output "region" {
  description = "Staging region."
  value       = var.region
}
output "service_name" {
  description = "Cloud Run service the staging tool updates."
  value       = google_cloud_run_v2_service.app.name
}
output "url" {
  description = "Staging URL reachable only from scanner sources."
  value       = "http://${google_compute_global_address.staging.address}"
}
output "repository_url" {
  description = "Artifact Registry prefix for staging images, or null when not created."
  value       = var.create_repository ? local.registry_prefix : null
}
output "security_policy" {
  description = "Cloud Armor policy in front of staging."
  value       = google_compute_security_policy.scanner.name
}
output "tool_config" {
  description = "Target entry for the staging tool config file (gcp)."
  value = {
    cloud                  = "gcp"
    environment            = "staging" # 초기화 Job 은 staging 대상에만 허용된다(운영 DB 보호)
    project_id             = var.project_id
    region                 = var.region
    service                = google_cloud_run_v2_service.app.name
    container_name         = var.container_name
    security_policy        = google_compute_security_policy.scanner.name
    dynamic_scanner_access = var.dynamic_scanner_access
    scanner_rule_priority  = local.scanner_rule_priority
    address                = google_compute_global_address.staging.name
    health_check_path      = var.health_check_path
    allowed_image_prefixes = local.allowed_image_prefixes
    reset_job              = var.database_enabled ? google_cloud_run_v2_job.db_reset[0].name : null
  }
}
