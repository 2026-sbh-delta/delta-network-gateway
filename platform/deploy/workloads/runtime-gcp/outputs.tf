output "connection" {
  value = {
    identity            = { revision = "v1", gcp_project = var.project_id }
    ssh_service_account = google_service_account.accounts["deployer"].email
    artifact_repository = "asia-northeast3-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.app.repository_id}/app"
    terraform = {
      project_id           = var.project_id
      zone                 = var.zone
      subnetwork           = data.google_compute_subnetwork.app.self_link
      service_account      = google_service_account.accounts["app"].email
      image                = var.runtime_image
      network_tag          = "delta-platform-customer-app"
      preinstalled_runtime = true
    }
  }
}
output "worker_database_address" { value = google_compute_address.worker_database.address }
output "credentials" {
  value = {
    type                              = "external_account"
    audience                          = "//iam.googleapis.com/${google_iam_workload_identity_pool_provider.aws.name}"
    subject_token_type                = "urn:ietf:params:aws:token-type:aws4_request"
    token_url                         = "https://sts.googleapis.com/v1/token"
    service_account_impersonation_url = "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/${google_service_account.accounts["deployer"].email}:generateAccessToken"
    credential_source = {
      environment_id                 = "aws1"
      region_url                     = "http://169.254.169.254/latest/meta-data/placement/availability-zone"
      url                            = "http://169.254.169.254/latest/meta-data/iam/security-credentials"
      imdsv2_session_token_url       = "http://169.254.169.254/latest/api/token"
      regional_cred_verification_url = "https://sts.{region}.amazonaws.com?Action=GetCallerIdentity&Version=2011-06-15"
    }
  }
}

output "database_address" { value = var.database_address }
