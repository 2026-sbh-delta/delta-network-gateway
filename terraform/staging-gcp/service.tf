resource "google_project_service" "enabled" {
  for_each           = var.enable_services ? local.services : toset([])
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "app" {
  count                  = var.create_repository ? 1 : 0
  location               = var.region
  repository_id          = local.prefix
  format                 = "DOCKER"
  cleanup_policy_dry_run = false
  docker_config {
    immutable_tags = true
  }
  cleanup_policies {
    id     = "keep-latest-20"
    action = "KEEP"
    most_recent_versions {
      keep_count = 20
    }
  }
  cleanup_policies {
    id     = "delete-the-rest"
    action = "DELETE"
    condition {
      tag_state = "ANY"
    }
  }
  depends_on = [google_project_service.enabled]
}

# Runtime identity has only its app secret access and Cloud SQL Client permissions.
resource "google_service_account" "app" {
  account_id   = "${var.name}-${var.environment}-stg-run"
  display_name = "Delta staging runtime"
  depends_on   = [google_project_service.enabled]
}

# The staging tool swaps the image with a new revision per run. Terraform owns only the base.
# Only the external load balancer reaches the service (ingress), and Cloud Armor in front of it admits only
# scanner sources, so invoker IAM is off: the scanner browser sends plain requests like it does on AWS.
# max 1 per revision: a swap starts the new revision next to the old one; traffic moves when it is ready.
resource "google_cloud_run_v2_service" "app" {
  name                 = local.prefix
  location             = var.region
  ingress              = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
  invoker_iam_disabled = true
  deletion_protection  = false
  template {
    service_account                  = google_service_account.app.email
    max_instance_request_concurrency = 80
    scaling {
      min_instance_count = var.min_instances
      max_instance_count = 1
    }
    dynamic "volumes" {
      for_each = var.database_enabled && !var.dual_container ? [1] : []
      content {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [google_sql_database_instance.app[0].connection_name]
        }
      }
    }
    containers {
      name       = local.ingress_container_name
      image      = var.bootstrap_image
      depends_on = var.dual_container ? ["backend"] : []
      ports {
        container_port = local.ingress_container_port
      }
      dynamic "env" {
        for_each = merge({ SERVED_BY = "gcp", CLOUD_PROVIDER = "gcp", APP_ENV = "staging" }, var.dual_container ? {
          API_HOST = "127.0.0.1", API_PORT = "8080"
        } : {})
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = var.database_enabled && !var.dual_container ? [1] : []
        content {
          name = "DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.database_url[0].secret_id
              version = "latest"
            }
          }
        }
      }
      dynamic "volume_mounts" {
        for_each = var.database_enabled && !var.dual_container ? [1] : []
        content {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }
      }
      resources {
        limits   = { cpu = var.cpu, memory = var.memory }
        cpu_idle = true
      }
      startup_probe {
        period_seconds    = 2
        timeout_seconds   = 1
        failure_threshold = 30
        http_get {
          path = var.health_check_path
          port = local.ingress_container_port
        }
      }
    }
    dynamic "containers" {
      for_each = var.dual_container ? [1] : []
      content {
        name       = "backend"
        image      = var.bootstrap_backend_image
        depends_on = var.database_enabled ? ["cloud-sql-proxy"] : []
        # No ports block: only the FE is an ingress container.
        dynamic "env" {
          for_each = merge({ CLOUD_PROVIDER = "gcp", APP_ENV = "staging", PORT = "8080" },
          var.database_enabled ? merge(local.node_database_environment, { PGUSER = "board_app" }) : {})
          content {
            name  = env.key
            value = env.value
          }
        }
        dynamic "env" {
          for_each = var.database_enabled ? {
            DATABASE_URL = google_secret_manager_secret.database_url[0].secret_id
            PGPASSWORD   = google_secret_manager_secret.runtime_password[0].secret_id
          } : {}
          content {
            name = env.key
            value_source {
              secret_key_ref {
                secret  = env.value
                version = "latest"
              }
            }
          }
        }
        resources {
          limits   = { cpu = var.cpu, memory = var.memory }
          cpu_idle = true
        }
        startup_probe {
          period_seconds    = 2
          timeout_seconds   = 1
          failure_threshold = 30
          http_get {
            path = "/healthz"
            port = 8080
          }
        }
      }
    }
    dynamic "containers" {
      for_each = var.dual_container && var.database_enabled ? [1] : []
      content {
        name  = "cloud-sql-proxy"
        image = var.cloud_sql_proxy_image
        args = ["--address=127.0.0.1", "--port=5432", "--health-check", "--http-address=0.0.0.0", "--http-port=9090",
        "--lazy-refresh", google_sql_database_instance.app[0].connection_name]
        resources {
          limits   = { cpu = "1", memory = "256Mi" }
          cpu_idle = true
        }
        startup_probe {
          period_seconds    = 2
          timeout_seconds   = 1
          failure_threshold = 30
          http_get {
            path = "/startup"
            port = 9090
          }
        }
      }
    }
  }
  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }
  lifecycle {
    ignore_changes = [template[0].containers[0].image, template[0].containers[1].image, template[0].labels, client, client_version]
  }
  # 런타임 계정의 DB·비밀값 권한이 먼저 있어야 새 개정판이 뜬다.
  # Provision owner must publish the separate runtime secret versions before service rollout.
  depends_on = [google_project_service.enabled, google_project_iam_member.app_sql_client,
  google_secret_manager_secret_iam_member.app_database_url, google_secret_manager_secret_iam_member.app_password]
}
