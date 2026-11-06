# 서비스 도메인 입구(11/6). Cloudflare DNS 전용 CNAME(app) 하나를 AWS·GCP 입구 이름 사이에서 바꿔 옮긴다.
# DNS 전용이라 TLS 는 각 입구에서 끝난다. 그래서 두 입구 모두 서비스 도메인 인증서를 미리 갖는다.
#   GCP: 운영 앱 VM(8088) 앞 전역 외부 HTTPS LB + Certificate Manager 관리형 인증서(DNS 인증).
#   AWS: ACM 인증서(DNS 검증)만 만든다. 호원님 앱 ALB 에 붙이는 것은 terraform/workload 의 certificate_arn 이다.
# app 레코드의 대상은 파이프라인 도메인 전환 단계가 바꾸므로 여기서 관리하지 않는다.

locals {
  domains = toset([var.service_domain, var.blue_domain])
  name    = "delta-release-app"
}

resource "google_project_service" "certificatemanager" {
  service            = "certificatemanager.googleapis.com"
  disable_on_destroy = false
}

# ── GCP 입구 ─────────────────────────────────────────
# 배포기는 같은 대상에 같은 VM 을 재사용한다. VM 을 새로 만들면(environment_id 변경) 이 루트를 다시 apply 한다.
data "google_compute_instance" "app" {
  name = var.app_instance
  zone = var.zone
}

resource "google_compute_instance_group" "app" {
  name      = local.name
  zone      = var.zone
  network   = data.google_compute_instance.app.network_interface[0].network
  instances = [data.google_compute_instance.app.self_link]
  named_port {
    name = "http"
    port = 8088
  }
}

# 상태 확인 출발지(130.211.0.0/22, 35.191.0.0/16 → 8088)는 release-runtime-gcp 의 delta-platform-app-lb 규칙이 연다.
resource "google_compute_health_check" "app" {
  name                = local.name
  check_interval_sec  = 10
  timeout_sec         = 5
  healthy_threshold   = 2
  unhealthy_threshold = 3
  http_health_check {
    port         = 8088
    request_path = var.health_path
  }
  log_config { enable = false }
}

resource "google_compute_backend_service" "app" {
  name                  = local.name
  protocol              = "HTTP"
  port_name             = "http"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  timeout_sec           = 30
  health_checks         = [google_compute_health_check.app.id]
  # 전환 확인이 응답한 쪽을 구분하는 표시(파이프라인 expect_served_by = "gcp").
  custom_response_headers = ["X-Served-By: gcp"]
  backend {
    group           = google_compute_instance_group.app.id
    balancing_mode  = "UTILIZATION"
    capacity_scaler = 1
  }
  log_config {
    enable      = true
    sample_rate = 1
  }
}

resource "google_compute_url_map" "app" {
  name            = local.name
  default_service = google_compute_backend_service.app.id
}

resource "google_certificate_manager_dns_authorization" "app" {
  for_each   = local.domains
  name       = "delta-${replace(each.key, ".", "-")}"
  domain     = each.key
  depends_on = [google_project_service.certificatemanager]
}

resource "google_certificate_manager_certificate" "app" {
  name = local.name
  managed {
    domains            = sort(local.domains)
    dns_authorizations = [for d in sort(local.domains) : google_certificate_manager_dns_authorization.app[d].id]
  }
}

resource "google_certificate_manager_certificate_map" "app" {
  name       = local.name
  depends_on = [google_project_service.certificatemanager]
}

resource "google_certificate_manager_certificate_map_entry" "app" {
  for_each     = local.domains
  name         = "delta-${replace(each.key, ".", "-")}"
  map          = google_certificate_manager_certificate_map.app.name
  certificates = [google_certificate_manager_certificate.app.id]
  hostname     = each.key
}

resource "google_compute_global_address" "app" {
  name = local.name
}

resource "google_compute_target_https_proxy" "app" {
  name            = local.name
  url_map         = google_compute_url_map.app.id
  certificate_map = "//certificatemanager.googleapis.com/${google_certificate_manager_certificate_map.app.id}"
}

resource "google_compute_global_forwarding_rule" "https" {
  name                  = "${local.name}-https"
  target                = google_compute_target_https_proxy.app.id
  ip_address            = google_compute_global_address.app.address
  port_range            = "443"
  load_balancing_scheme = "EXTERNAL_MANAGED"
}

# http 는 https 로 보낸다(AWS ALB 의 80 → 443 과 같게).
resource "google_compute_url_map" "redirect" {
  name = "${local.name}-redirect"
  default_url_redirect {
    https_redirect = true
    strip_query    = false
  }
}
resource "google_compute_target_http_proxy" "redirect" {
  name    = "${local.name}-redirect"
  url_map = google_compute_url_map.redirect.id
}
resource "google_compute_global_forwarding_rule" "http" {
  name                  = "${local.name}-http"
  target                = google_compute_target_http_proxy.redirect.id
  ip_address            = google_compute_global_address.app.address
  port_range            = "80"
  load_balancing_scheme = "EXTERNAL_MANAGED"
}

# ── AWS 입구 인증서 ──────────────────────────────────
resource "aws_acm_certificate" "app" {
  domain_name               = var.service_domain
  subject_alternative_names = [var.green_domain]
  validation_method         = "DNS"
  lifecycle { create_before_destroy = true }
}

resource "aws_acm_certificate_validation" "app" {
  certificate_arn         = aws_acm_certificate.app.arn
  validation_record_fqdns = [for r in cloudflare_dns_record.acm : r.name]
}

# ── Cloudflare 레코드(모두 DNS 전용) ─────────────────
resource "cloudflare_dns_record" "blue" {
  zone_id = var.cloudflare_zone_id
  name    = var.blue_domain
  type    = "A"
  content = google_compute_global_address.app.address
  ttl     = 60
  proxied = false
  comment = "delta service-domain: GCP 운영 LB"
}

resource "cloudflare_dns_record" "green" {
  zone_id = var.cloudflare_zone_id
  name    = var.green_domain
  type    = "CNAME"
  content = var.green_origin
  ttl     = 60
  proxied = false
  comment = "delta service-domain: AWS 운영 ALB"
}

resource "cloudflare_dns_record" "gcp_auth" {
  for_each = local.domains
  zone_id  = var.cloudflare_zone_id
  name     = trimsuffix(google_certificate_manager_dns_authorization.app[each.key].dns_resource_record[0].name, ".")
  type     = google_certificate_manager_dns_authorization.app[each.key].dns_resource_record[0].type
  content  = trimsuffix(google_certificate_manager_dns_authorization.app[each.key].dns_resource_record[0].data, ".")
  ttl      = 60
  proxied  = false
  comment  = "delta service-domain: GCP 인증서 DNS 인증"
}

resource "cloudflare_dns_record" "acm" {
  for_each = {
    for o in aws_acm_certificate.app.domain_validation_options : o.domain_name => o
  }
  zone_id = var.cloudflare_zone_id
  name    = trimsuffix(each.value.resource_record_name, ".")
  type    = each.value.resource_record_type
  content = trimsuffix(each.value.resource_record_value, ".")
  ttl     = 60
  proxied = false
  comment = "delta service-domain: ACM 검증"
}

# 11/6 이전에 손으로 만든 자리표시자(aws → example.com)를 가져와 대상을 고친다.
import {
  to = cloudflare_dns_record.green
  id = "${var.cloudflare_zone_id}/59f8a67761b0e0380a46ebab4be2da98"
}
