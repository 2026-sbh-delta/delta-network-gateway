mock_provider "google" {
  override_during = plan
  mock_data "google_project" { defaults = { number = "366844544284" } }
  mock_data "google_compute_image" { defaults = { self_link = "https://www.googleapis.com/compute/v1/projects/ubuntu-os-cloud/global/images/ubuntu-2404-noble-amd64-v20261024" } }
  mock_resource "google_service_account" { defaults = { email = "mock@term2-delta.iam.gserviceaccount.com" } }
  mock_resource "google_sql_database_instance" {
    defaults = {
      dns_name                    = "example.asia-northeast3.sql.goog."
      connection_name             = "term2-delta:asia-northeast3:delta-board-gcp-db"
      psc_service_attachment_link = "projects/producer/regions/asia-northeast3/serviceAttachments/sql"
    }
  }
}
variables {
  network = {
    network = "projects/term2-delta/global/networks/delta-lab-workload"
    subnets = { frontend = "projects/term2-delta/regions/asia-northeast3/subnetworks/fe", backend = "projects/term2-delta/regions/asia-northeast3/subnetworks/be", database = "projects/term2-delta/regions/asia-northeast3/subnetworks/db", proxy = "projects/term2-delta/regions/asia-northeast3/subnetworks/proxy" }
    cidrs   = { frontend = "10.32.1.0/24", backend = "10.32.2.0/24", database = "10.32.3.0/24", proxy = "10.32.4.0/23" }
  }
}
run "private_x86_application_with_psc" {
  command = plan
  variables {
    single_vm_compose = false
    compose_host_access = false
    backend_deletion_protection = true
  }
  assert {
    condition     = alltrue([for vm in google_compute_instance.vm : vm.machine_type == "e2-small" && length(vm.network_interface[0].access_config) == 0]) && google_compute_instance.vm["frontend"].network_interface[0].subnetwork != google_compute_instance.vm["backend"].network_interface[0].subnetwork
    error_message = "Use separate private x86 VMs."
  }
  assert {
    condition     = google_sql_database_instance.database.settings[0].ip_configuration[0].ipv4_enabled == false && google_sql_database_instance.database.settings[0].ip_configuration[0].psc_config[0].psc_enabled && google_sql_database_instance.database.root_password == null
    error_message = "Database must use private PSC without a password in Terraform."
  }
  assert {
    condition     = google_compute_forwarding_rule.frontend.load_balancing_scheme == "EXTERNAL_MANAGED" && google_compute_forwarding_rule.frontend.port_range == "80" && google_compute_forwarding_rule.database.load_balancing_scheme == "" && google_compute_address.database.address == "10.32.3.10"
    error_message = "ALB and private database endpoint must have distinct purposes."
  }
  assert {
    condition     = google_storage_bucket.releases.public_access_prevention == "enforced" && google_secret_manager_secret_iam_member.app.member == "serviceAccount:${google_service_account.vm["backend"].email}"
    error_message = "Prevent public artifacts and restrict DB credentials to BE."
  }
}
run "reject_arm_machine" {
  command = plan
  variables { machine_type = "t2a-standard-1" }
  expect_failures = [var.machine_type]
}
run "reject_arm_image" {
  command = plan
  variables { image = "ubuntu-2404-noble-arm64-v20261024" }
  expect_failures = [var.image]
}
run "reject_project_number" {
  command = plan
  variables { project_number = "123456789012" }
  expect_failures = [terraform_data.contract]
}
run "reject_wrong_project" {
  command = plan
  variables { project_id = "other-project" }
  expect_failures = [var.project_id]
}
run "single_private_compose_host" {
  command = plan
  variables {
    single_vm_compose = true
    compose_host_access = true
    backend_deletion_protection = false
  }
  assert {
    condition = length(google_compute_instance.vm) == 1 && google_compute_instance.vm["frontend"].network_interface[0].network_ip == "10.32.1.20" && google_compute_instance.vm["frontend"].deletion_protection
    error_message = "Keep the same private, deletion-protected frontend host only."
  }
  assert {
    condition = length(google_secret_manager_secret_iam_member.compose_app) == 1 && google_secret_manager_secret_iam_member.compose_app[0].member == "serviceAccount:${google_service_account.vm["frontend"].email}" && google_project_iam_member.compose_sql_client[0].condition[0].expression == "resource.name == 'projects/term2-delta/instances/delta-board-gcp-db'"
    error_message = "Compose host needs only app-secret and application-database access."
  }
}
run "reject_compose_without_host_access" {
  command = plan
  variables {
    single_vm_compose = true
    compose_host_access = false
  }
  expect_failures = [terraform_data.contract]
}
