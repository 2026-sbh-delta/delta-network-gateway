mock_provider "google" {
  override_during = plan
}
mock_provider "random" {
  override_during = plan
}

# Resolve only the computed identity, not the service/job fields under test.
# Without this override a plan cannot evaluate their identity separation.
override_resource {
  override_during = plan
  target          = google_service_account.app
  values          = { email = "staging-app@delta-example-01.iam.gserviceaccount.com" }
}

variables {
  project_id = "delta-example-01"
  # 기존 단일 컨테이너 검사는 이 값으로 유지한다. FE+BE 검사는 각 run에서 true로 바꾼다.
  dual_container = false
}

run "defaults_build_a_scanner_only_staging" {
  command = plan

  assert {
    condition     = google_cloud_run_v2_service.app.ingress == "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
    error_message = "Only the load balancer may reach Cloud Run."
  }
  assert {
    condition     = one([for r in google_compute_security_policy.scanner.rule : r.action if r.priority == 2147483647]) == "deny(403)"
    error_message = "The default Cloud Armor rule must deny."
  }
  assert {
    condition     = length(google_compute_security_policy_rule.fixed_scanners) == 0
    error_message = "No fixed scanner rule without scanner_public_cidrs."
  }
  assert {
    condition     = output.tool_config.cloud == "gcp" && output.tool_config.dynamic_scanner_access
    error_message = "tool_config must describe a gcp target with dynamic scanner access."
  }
  assert {
    condition     = length(output.tool_config.allowed_image_prefixes) == 1 && output.tool_config.allowed_image_prefixes[0] == "asia-northeast3-docker.pkg.dev/delta-example-01/delta-lab-staging/"
    error_message = "The tool must accept only this project's staging repository by default."
  }
}

run "fixed_scanner_cidrs_get_their_own_rule" {
  command = plan
  variables {
    dynamic_scanner_access = false
    scanner_public_cidrs   = ["203.0.113.0/24"]
  }

  assert {
    condition     = google_compute_security_policy_rule.fixed_scanners[0].priority == 1001
    error_message = "Fixed CIDRs sit behind the tool-managed rule."
  }
}

run "nothing_can_reach_without_scanner_sources" {
  command = plan
  variables {
    dynamic_scanner_access = false
  }
  expect_failures = [terraform_data.contract]
}

run "open_cidrs_are_rejected" {
  command = plan
  variables {
    scanner_public_cidrs = ["0.0.0.0/0"]
  }
  expect_failures = [var.scanner_public_cidrs]
}

# tracecompose-delivery-20261106: this checks an existing resource, not a new input.
# PostgreSQL 16 must fail the approved default-major contract before implementation.
run "staging_database_defaults_to_postgres18" {
  command = plan
  assert {
    condition     = google_sql_database_instance.app[0].database_version == "POSTGRES_18"
    error_message = "Staging must default to PostgreSQL 18; POSTGRES_16 cannot satisfy the approved deployment contract."
  }
}

run "single_container_default_remains_compatible" {
  command = plan
  assert {
    condition = (
      length(google_cloud_run_v2_service.app.template[0].containers) == 1 &&
      google_cloud_run_v2_service.app.template[0].containers[0].name == "app" &&
      google_cloud_run_v2_service.app.template[0].containers[0].image == var.bootstrap_image &&
      one(google_cloud_run_v2_service.app.template[0].containers[0].ports).container_port == 8080 &&
      output.tool_config.container_name == "app"
    )
    error_message = "Omitting dual_container must retain the existing app/8080 single ingress container."
  }
}

# Same opt-in/image input contract as AWS. An optional cloud-sql-proxy is infrastructure,
# not a third app image; it must remain a sidecar and precede BE when present.
run "dual_bootstrap_has_frontend_ingress_and_backend_sidecar" {
  command = plan
  variables {
    dual_container          = true
    container_name          = "frontend"
    container_port          = 3000
    bootstrap_image         = "asia-northeast3-docker.pkg.dev/delta-example-01/delta-lab-staging/frontend@sha256:1111111111111111111111111111111111111111111111111111111111111111"
    bootstrap_backend_image = "asia-northeast3-docker.pkg.dev/delta-example-01/delta-lab-staging/backend@sha256:2222222222222222222222222222222222222222222222222222222222222222"
    dynamic_scanner_access  = false
    scanner_public_cidrs    = ["54.116.158.85/32"]
  }
  assert {
    condition = (
      contains([2, 3], length(google_cloud_run_v2_service.app.template[0].containers)) &&
      length([for c in google_cloud_run_v2_service.app.template[0].containers : c if c.name == "frontend"]) == 1 &&
      length([for c in google_cloud_run_v2_service.app.template[0].containers : c if c.name == "backend"]) == 1 &&
      alltrue([for c in google_cloud_run_v2_service.app.template[0].containers : contains(["frontend", "backend", "cloud-sql-proxy"], c.name)])
    )
    error_message = "One Cloud Run revision must hold FE and BE, plus only an optional cloud-sql-proxy infrastructure sidecar."
  }
  assert {
    condition = try(
      one([for c in google_cloud_run_v2_service.app.template[0].containers : c.image if c.name == "frontend"]) == var.bootstrap_image &&
      one([for c in google_cloud_run_v2_service.app.template[0].containers : c.image if c.name == "backend"]) == "asia-northeast3-docker.pkg.dev/delta-example-01/delta-lab-staging/backend@sha256:2222222222222222222222222222222222222222222222222222222222222222" &&
      one([for c in google_cloud_run_v2_service.app.template[0].containers : one(c.ports).container_port if c.name == "frontend"]) == 3000 &&
      alltrue([for c in google_cloud_run_v2_service.app.template[0].containers : length(c.ports) == 0 if c.name != "frontend"]),
      false
    )
    error_message = "Keep both immutable image refs; FE3000 must be the sole ingress, never BE8080 or the SQL proxy."
  }
  assert {
    condition = try(
      one([for c in google_cloud_run_v2_service.app.template[0].containers : { for e in c.env : e.name => e.value } if c.name == "frontend"])["API_HOST"] == "127.0.0.1" &&
      one([for c in google_cloud_run_v2_service.app.template[0].containers : { for e in c.env : e.name => e.value } if c.name == "frontend"])["API_PORT"] == "8080" &&
      alltrue([for c in google_cloud_run_v2_service.app.template[0].containers :
        { for e in c.env : e.name => e.value }["CLOUD_PROVIDER"] == "gcp" &&
        { for e in c.env : e.name => e.value }["APP_ENV"] == "staging"
      if contains(["frontend", "backend"], c.name)]),
      false
    )
    error_message = "Node FE must reach the sidecar on 127.0.0.1:8080; both app containers must identify gcp/staging."
  }
  assert {
    condition = try(
      contains(one([for c in google_cloud_run_v2_service.app.template[0].containers : c.depends_on if c.name == "frontend"]), "backend") &&
      length(one([for c in google_cloud_run_v2_service.app.template[0].containers : c.startup_probe if c.name == "backend"])) == 1 &&
      one(one([for c in google_cloud_run_v2_service.app.template[0].containers : c.startup_probe if c.name == "backend"])).http_get[0].port == 8080 &&
      alltrue([for proxy in google_cloud_run_v2_service.app.template[0].containers :
        contains(one([for c in google_cloud_run_v2_service.app.template[0].containers : c.depends_on if c.name == "backend"]), proxy.name) &&
        length(proxy.startup_probe) == 1
      if proxy.name == "cloud-sql-proxy"]),
      false
    )
    error_message = "Startup order must be SQL proxy (when used) -> healthy BE8080 -> FE; FE-only readiness is insufficient."
  }
  assert {
    condition = (
      google_cloud_run_v2_service.app.ingress == "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER" &&
      one([for r in google_compute_security_policy.scanner.rule : r.action if r.priority == 2147483647]) == "deny(403)" &&
      google_compute_security_policy_rule.fixed_scanners[0].action == "allow" &&
      toset(google_compute_security_policy_rule.fixed_scanners[0].match[0].config[0].src_ip_ranges) == toset(["54.116.158.85/32"])
    )
    error_message = "Dual Cloud Run must retain load-balancer ingress and the scanner NAT /32 above Cloud Armor default deny."
  }
  assert {
    condition = try(
      output.tool_config.container_name == "frontend" &&
      output.tool_config.backend_container_name == "backend" &&
      output.tool_config.expected_database_major == 18 &&
      output.tool_config.database_name == "board_staging" &&
      output.tool_config.environment == "staging",
      false
    )
    error_message = "tool_config must expose the two replaceable app names and the staging DB18 identity for provider preflight."
  }
  assert {
    condition = alltrue([for c in google_cloud_run_v2_service.app.template[0].containers :
      alltrue([for e in c.env :
        !contains(["DATABASE_URL", "DB_PASSWORD", "RESET_DATABASE_URL"], e.name) ||
        (c.name == "backend" && e.value == null && length(e.value_source) == 1)
      ])
    ])
    error_message = "DB values must be secret references available only to BE; FE and proxy must not inherit app/reset credentials."
  }
}

# Existing job/resources make these genuine plan assertions even before Node support exists.
# Runtime checks still must prove packaged migration SQL, actual server major 18,
# schema readiness, zero app rows and nonzero exit on schema/reset failure.
# A planned command or APP_ENV value does not establish any of those observations.
run "node_reset_uses_backend_image_and_separate_credentials" {
  command = plan
  variables {
    dual_container          = true
    bootstrap_backend_image = "asia-northeast3-docker.pkg.dev/delta-example-01/delta-lab-staging/backend@sha256:2222222222222222222222222222222222222222222222222222222222222222"
  }
  assert {
    condition = (
      google_sql_database_instance.app[0].database_version == "POSTGRES_18" &&
      google_sql_database.app[0].name == "board_staging" &&
      length(google_sql_database_instance.app[0].settings[0].ip_configuration[0].authorized_networks) == 0
    )
    error_message = "Reset must target a separate board_staging DB on PostgreSQL18 with no open authorized networks."
  }
  assert {
    condition = (
      google_cloud_run_v2_job.db_reset[0].template[0].template[0].containers[0].image == "asia-northeast3-docker.pkg.dev/delta-example-01/delta-lab-staging/backend@sha256:2222222222222222222222222222222222222222222222222222222222222222" &&
      google_cloud_run_v2_job.db_reset[0].template[0].template[0].containers[0].command == tolist(["node", "scripts/reset-db.mjs"])
    )
    error_message = "The existing reset Job must execute the packaged Node BE reset entrypoint from the BE image, not python -m app.reset."
  }
  assert {
    condition = (
      google_cloud_run_v2_job.db_reset[0].template[0].task_count == 1 &&
      google_cloud_run_v2_job.db_reset[0].template[0].template[0].max_retries == 0 &&
      one([for e in google_cloud_run_v2_job.db_reset[0].template[0].template[0].containers[0].env : e.value if e.name == "APP_ENV"]) == "staging"
    )
    error_message = "Reset must run once without retries and be staging-guarded."
  }
  assert {
    condition = try(
      length([for c in google_cloud_run_v2_service.app.template[0].containers : c if c.name == "backend"]) == 1 &&
      one([for e in google_cloud_run_v2_job.db_reset[0].template[0].template[0].containers[0].env : e.value if e.name == "DATABASE_URL"]) == null &&
      one([for e in google_cloud_run_v2_job.db_reset[0].template[0].template[0].containers[0].env : e.value_source[0].secret_key_ref[0].secret if e.name == "PGPASSWORD"]) ==
      one([for c in google_cloud_run_v2_service.app.template[0].containers :
        one([for e in c.env : e.value_source[0].secret_key_ref[0].secret if e.name == "PGPASSWORD"])
      if c.name == "backend"]),
      false
    )
    error_message = "Reset and BE must read the same Terraform-managed DB user secret, never a literal value."
  }
  assert {
    condition = try(
      output.tool_config.reset_job == google_cloud_run_v2_job.db_reset[0].name &&
      output.tool_config.expected_database_major == 18 &&
      output.tool_config.environment == "staging",
      false
    )
    error_message = "Provider preflight needs the existing reset Job plus an explicit staging/DB18 contract."
  }
}

run "database_disabled_has_no_reset_job" {
  command = plan
  variables {
    database_enabled = false
  }
  assert {
    condition = (
      length(google_sql_database_instance.app) == 0 &&
      length(google_cloud_run_v2_job.db_reset) == 0 &&
      output.tool_config.reset_job == null
    )
    error_message = "Disabling the existing DB option must still remove its DB and reset Job."
  }
}
