output "gcp_address" {
  value = google_compute_global_address.app.address
}

output "acm_certificate_arn" {
  description = "AWS 앱 ALB 에 붙일 인증서(terraform/workload application.certificate_arn, hostname = 서비스 도메인)."
  value       = aws_acm_certificate_validation.app.certificate_arn
}

# 오케스트레이터 DELTA_CUTOVER. deploy.sh 가 SSM orchestrator/cutover_config 로 올린다.
output "cutover_config" {
  value = jsonencode({
    zone_id           = var.cloudflare_zone_id
    record            = var.service_domain
    green             = var.green_domain
    blue              = var.blue_domain
    blue_origin       = "https://${var.blue_domain}"
    verify_path       = var.health_path
    expect_served_by  = "gcp"
    green_verify_path = var.green_health_path
  })
}
