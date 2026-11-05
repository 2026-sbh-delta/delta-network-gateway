locals {
  target = {
    cloud                   = "gcp"
    project                 = var.project_id
    region                  = var.region
    service_prefix          = "delta-sbx"
    repository              = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.sandbox.repository_id}/app"
    runtime_service_account = google_service_account.accounts["runtime"].email
    runner_service_account  = google_service_account.accounts["runner"].email
    scanner_service_account = google_service_account.accounts["scanner"].email
    network                 = google_compute_network.sandbox.id
    subnet                  = google_compute_subnetwork.sandbox.id
    network_tag             = "delta-sandbox"
    allowed_image_prefixes  = ["${var.aws_account_id}.dkr.ecr.ap-northeast-2.amazonaws.com/delta-platform-builds@"]
  }
  credentials = { for key, role in local.aws_roles : key => {
    type                              = "external_account"
    audience                          = "//iam.googleapis.com/${google_iam_workload_identity_pool_provider.aws.name}"
    subject_token_type                = "urn:ietf:params:aws:token-type:aws4_request"
    token_url                         = "https://sts.googleapis.com/v1/token"
    service_account_impersonation_url = "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/${google_service_account.accounts[key].email}:generateAccessToken"
    credential_source = {
      environment_id                 = "aws1"
      region_url                     = "http://169.254.169.254/latest/meta-data/placement/availability-zone"
      url                            = "http://169.254.169.254/latest/meta-data/iam/security-credentials"
      imdsv2_session_token_url       = "http://169.254.169.254/latest/api/token"
      regional_cred_verification_url = "https://sts.{region}.amazonaws.com?Action=GetCallerIdentity&Version=2011-06-15"
    }
  } }
}
output "runtime_env" {
  value = {
    PLATFORM_GCP_SANDBOX_TARGET   = jsonencode(local.target)
    PLATFORM_GCP_CREDENTIALS_JSON = jsonencode(local.credentials.runner)
  }
}
output "dast_env" {
  value = {
    DAST_GCP_TARGET           = jsonencode(local.target)
    DAST_GCP_CREDENTIALS_JSON = jsonencode(local.credentials.scanner)
  }
}
output "subnet_cidr" { value = google_compute_subnetwork.sandbox.ip_cidr_range }
