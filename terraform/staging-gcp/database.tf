# 스테이징 전용. 기존 Cloud SQL 16 인스턴스는 새 18 인스턴스로 교체된다(시험 데이터만 있어 보존하지 않는다).
resource "google_sql_database_instance" "app" {
  count               = var.database_enabled ? 1 : 0
  name                = "${local.prefix}-db-pg18"
  database_version    = "POSTGRES_18"
  region              = var.region
  deletion_protection = false
  settings {
    edition           = "ENTERPRISE"
    tier              = "db-f1-micro"
    availability_type = "ZONAL"
    disk_type         = "PD_HDD"
    disk_size         = 10
    disk_autoresize   = false
    backup_configuration {
      enabled = false
    }
    ip_configuration {
      ipv4_enabled = true
      ssl_mode     = "ENCRYPTED_ONLY"
      # No authorized networks. Auth Proxy uses IAM and TLS on the external leg.
    }
    user_labels = local.common_labels
  }
  lifecycle {
    create_before_destroy = true
  }
  depends_on = [google_project_service.enabled]
}
resource "google_sql_database" "app" {
  count    = var.database_enabled ? 1 : 0
  name     = local.database_name
  instance = google_sql_database_instance.app[0].name
}

# 스테이징 DB 사용자 하나를 앱과 초기화 작업이 함께 쓴다. 배포마다 비우는 시험 데이터만 담는다.
# Terraform이 비밀번호를 만들고 비밀값까지 채운다(사람이 SQL·비밀값을 넣는 단계 없음).
# Cloud SQL API로 만든 사용자는 cloudsqlsuperuser라 초기화 작업이 스키마를 만들 수 있다.
resource "random_password" "db" {
  count   = var.database_enabled ? 1 : 0
  length  = 32
  special = false
}
resource "google_sql_user" "app" {
  count    = var.database_enabled ? 1 : 0
  name     = local.database_user
  instance = google_sql_database_instance.app[0].name
  password = random_password.db[0].result
}
resource "google_secret_manager_secret" "database_url" {
  count     = var.database_enabled ? 1 : 0
  secret_id = "${local.prefix}-database-url"
  replication {
    auto {}
  }
  depends_on = [google_project_service.enabled]
}
resource "google_secret_manager_secret_version" "database_url" {
  count  = var.database_enabled ? 1 : 0
  secret = google_secret_manager_secret.database_url[0].id
  # Node는 PG* 환경을 쓰고(Proxy loopback), 예전 Flask 단일 컨테이너는 Unix socket URL을 쓴다.
  secret_data = (var.dual_container
    ? "postgresql://${local.database_user}:${random_password.db[0].result}@127.0.0.1:5432/${local.database_name}"
  : "postgresql://${local.database_user}:${random_password.db[0].result}@/${local.database_name}?host=/cloudsql/${google_sql_database_instance.app[0].connection_name}")
}
resource "google_secret_manager_secret" "runtime_password" {
  count     = var.database_enabled ? 1 : 0
  secret_id = "${local.prefix}-runtime-password"
  replication {
    auto {}
  }
  depends_on = [google_project_service.enabled]
}
resource "google_secret_manager_secret_version" "runtime_password" {
  count       = var.database_enabled ? 1 : 0
  secret      = google_secret_manager_secret.runtime_password[0].id
  secret_data = random_password.db[0].result
}
resource "google_project_iam_member" "app_sql_client" {
  count   = var.database_enabled ? 1 : 0
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.app.email}"
}
resource "google_secret_manager_secret_iam_member" "app_database_url" {
  count     = var.database_enabled ? 1 : 0
  secret_id = google_secret_manager_secret.database_url[0].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.app.email}"
}
resource "google_secret_manager_secret_iam_member" "app_password" {
  count     = var.database_enabled ? 1 : 0
  secret_id = google_secret_manager_secret.runtime_password[0].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.app.email}"
}
locals {
  node_database_environment = {
    STORAGE_BACKEND = "postgres"
    PGHOST          = "127.0.0.1"
    PGPORT          = "5432"
    PGDATABASE      = local.database_name
    # Only loopback is plaintext. Proxy -> Cloud SQL remains IAM-authenticated TLS.
    PGSSLMODE = "disable"
  }
  # 초기화 Job은 Cloud Run 내장 Cloud SQL 소켓으로 붙는다(프록시 컨테이너 기동·종료 비용 없음).
  reset_database_environment = merge(local.node_database_environment, {
    PGHOST = "/cloudsql/${try(google_sql_database_instance.app[0].connection_name, "")}"
  })
}
resource "google_cloud_run_v2_job" "db_reset" {
  count               = var.database_enabled ? 1 : 0
  name                = "${local.prefix}-db-reset"
  location            = var.region
  deletion_protection = false
  template {
    task_count  = 1
    parallelism = 1
    template {
      service_account = google_service_account.app.email
      max_retries     = 0
      timeout         = "120s"
      volumes {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [google_sql_database_instance.app[0].connection_name]
        }
      }
      containers {
        name    = "reset"
        image   = var.dual_container ? var.bootstrap_backend_image : var.bootstrap_image
        command = var.dual_container ? ["node", "scripts/reset-db.mjs"] : ["python", "-m", "app.reset"]
        dynamic "env" {
          for_each = merge({ APP_ENV = "staging" }, var.dual_container ? merge(local.reset_database_environment, {
            PGUSER             = local.database_user
            APP_RESET_DATABASE = local.database_name
          }) : {})
          content {
            name  = env.key
            value = env.value
          }
        }
        env {
          name = "DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.database_url[0].secret_id
              version = "latest"
            }
          }
        }
        dynamic "env" {
          for_each = var.dual_container ? [1] : []
          content {
            name = "PGPASSWORD"
            value_source {
              secret_key_ref {
                secret  = google_secret_manager_secret.runtime_password[0].secret_id
                version = "latest"
              }
            }
          }
        }
        volume_mounts {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }
      }
    }
  }
  lifecycle {
    ignore_changes = [template[0].template[0].containers[0].image, client, client_version]
  }
  depends_on = [google_secret_manager_secret_iam_member.app_database_url, google_secret_manager_secret_iam_member.app_password,
  google_project_iam_member.app_sql_client]
}
