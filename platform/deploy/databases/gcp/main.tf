terraform {
  required_version = ">= 1.11, < 2.0"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 7.0" }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

resource "google_sql_database_instance" "app" {
  name                = var.name
  region              = var.region
  database_version    = "POSTGRES_17"
  deletion_protection = true
  settings {
    edition                     = "ENTERPRISE"
    tier                        = "db-g1-small"
    availability_type           = "ZONAL"
    disk_type                   = "PD_SSD"
    disk_size                   = 10
    disk_autoresize             = true
    disk_autoresize_limit       = 20
    deletion_protection_enabled = true
    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = 1
      backup_retention_settings {
        retained_backups = 2
      }
    }
    ip_configuration {
      ipv4_enabled   = false
      ssl_mode       = "ENCRYPTED_ONLY"
      server_ca_mode = "GOOGLE_MANAGED_CAS_CA"
      psc_config {
        psc_enabled               = true
        allowed_consumer_projects = [var.project_id]
      }
    }
    database_flags {
      name  = "cloudsql.logical_decoding"
      value = "on"
    }
    user_labels = {
      managed-by = "delta-platform"
      purpose    = var.purpose
    }
  }
  lifecycle {
    prevent_destroy = true
  }
}

resource "google_compute_address" "database" {
  name         = var.name
  region       = var.region
  address_type = "INTERNAL"
  subnetwork   = var.subnetwork
}

resource "google_compute_forwarding_rule" "database" {
  name                    = var.name
  region                  = var.region
  network                 = var.network
  ip_address              = google_compute_address.database.self_link
  load_balancing_scheme   = ""
  target                  = google_sql_database_instance.app.psc_service_attachment_link
  allow_psc_global_access = true
}

resource "google_dns_managed_zone" "database" {
  name       = var.name
  dns_name   = google_sql_database_instance.app.dns_name
  visibility = "private"
  private_visibility_config {
    networks { network_url = var.network }
  }
}

resource "google_dns_record_set" "database" {
  name         = google_sql_database_instance.app.dns_name
  managed_zone = google_dns_managed_zone.database.name
  type         = "A"
  ttl          = 60
  rrdatas      = [google_compute_address.database.address]
}

resource "google_sql_user" "admin" {
  name                = "platform_admin"
  instance            = google_sql_database_instance.app.name
  password_wo         = var.admin_password
  password_wo_version = 1
}

resource "google_secret_manager_secret" "admin" {
  secret_id = "${var.name}-admin"
  replication {
    auto {}
  }
  labels = { managed-by = "delta-platform", purpose = var.purpose }
}

resource "google_secret_manager_secret_version" "admin" {
  secret                 = google_secret_manager_secret.admin.id
  secret_data_wo_version = 1
  secret_data_wo = jsonencode({
    host     = trimsuffix(google_sql_database_instance.app.dns_name, ".")
    hostaddr = google_compute_address.database.address
    port     = 5432
    dbname   = "postgres"
    username = google_sql_user.admin.name
    password = var.admin_password
    ca_pem   = var.ca_pem
  })
}
