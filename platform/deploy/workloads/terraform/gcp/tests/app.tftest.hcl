mock_provider "google" {}
variables {
  project_id      = "term2-delta"
  zone            = "asia-northeast3-a"
  subnetwork      = "projects/term2-delta/regions/asia-northeast3/subnetworks/test"
  service_account = "reader@term2-delta.iam.gserviceaccount.com"
  image           = "projects/debian-cloud/global/images/debian-12-test"
  network_tag     = "delta-app"
  environment_id  = "00000000-0000-0000-0000-000000000001"
  service_id      = "00000000-0000-0000-0000-000000000002"
  profile_id      = "e2-micro"
}
run "private_pinned_vm" {
  command = plan
  assert {
    condition     = length(google_compute_instance.app.network_interface[0].access_config) == 0 && google_compute_instance.app.metadata["enable-oslogin"] == "TRUE"
    error_message = "공인 IP 없이 OS Login을 사용해야 합니다."
  }
  assert {
    condition     = google_compute_instance.app.machine_type == var.profile_id && google_compute_instance.app.shielded_instance_config[0].enable_secure_boot
    error_message = "선택 사양과 Secure Boot가 필요합니다."
  }
}
