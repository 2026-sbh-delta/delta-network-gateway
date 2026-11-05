# A 기업 스테이징 DB: 게시판(PostgreSQL)용. 일반적인 상시 스테이징 DB 방식의 가장 작은 구성이다(시연 뒤 삭제).
# 허용 네트워크를 두지 않는다. Cloud Run 내장 Cloud SQL 연결(IAM 확인, unix 소켓)로만 접속한다.
# 점검 회차마다 배포 직전에 초기화 Job이 같은 이미지로 마이그레이션하고 데이터를 비운다(남은 행이 0이 아니면 실패).

resource "google_sql_database_instance" "app" {
  count               = var.database_enabled ? 1 : 0
  name                = "${local.prefix}-db"
  database_version    = "POSTGRES_16"
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
    }
    user_labels = local.common_labels
  }
  depends_on = [google_project_service.enabled]
}

resource "google_sql_database" "app" {
  count    = var.database_enabled ? 1 : 0
  name     = "board"
  instance = google_sql_database_instance.app[0].name
}

# 비밀번호는 A 기업 Terraform state(GCS, 접근 제한)에 남는다. 일반적인 관리형 DB 구성과 같다.
resource "random_password" "db" {
  count   = var.database_enabled ? 1 : 0
  length  = 32
  special = false
}

resource "google_sql_user" "app" {
  count    = var.database_enabled ? 1 : 0
  name     = "board"
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
  count       = var.database_enabled ? 1 : 0
  secret      = google_secret_manager_secret.database_url[0].id
  secret_data = "postgresql://board:${random_password.db[0].result}@/board?host=/cloudsql/${google_sql_database_instance.app[0].connection_name}"
}

# 런타임 계정: DB 연결과 이 비밀값 읽기만.
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

# 배포 직전 초기화: 같은 이미지로 스키마를 적용하고 게시글·댓글(DAST 페이로드 포함)을 비운다. 남은 행이 0이어야 성공.
resource "google_cloud_run_v2_job" "db_reset" {
  count               = var.database_enabled ? 1 : 0
  name                = "${local.prefix}-db-reset"
  location            = var.region
  deletion_protection = false
  template {
    task_count = 1
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
        name = "reset"
        # 앱 저장소의 초기화 코드(app.reset: 스키마 적용 → 비우기 → 남은 행 0 확인)를 배포 이미지 그대로 쓴다.
        # APP_ENV=staging 이 아니면 app.reset 이 거부한다. 운영에는 이 Job 을 만들지 않는다.
        image   = var.bootstrap_image
        command = ["python", "-m", "app.reset"]
        env {
          name  = "APP_ENV"
          value = "staging"
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
        volume_mounts {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }
      }
    }
  }
  lifecycle {
    # 배포할 때마다 스테이징 도구가 이미지를 배포 이미지로 바꾼다.
    ignore_changes = [template[0].template[0].containers[0].image, client, client_version]
  }
  depends_on = [google_secret_manager_secret_iam_member.app_database_url, google_project_iam_member.app_sql_client]
}
