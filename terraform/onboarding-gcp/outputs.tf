output "service_accounts" {
  description = "용도별 서비스 계정."
  value       = { for k, sa in google_service_account.this : k => sa.email }
}

output "workload_identity_provider" {
  value = "${local.pool_name}/providers/${google_iam_workload_identity_pool_provider.aws.workload_identity_pool_provider_id}"
}

# 플랫폼 인스턴스에 넣는 자격 증명 설정(비밀값 없음). 인스턴스 역할(IMDSv2)로 서명해 GCP 단기 토큰으로 바꾼다.
output "credential_configs" {
  description = "서비스 계정별 external_account 설정 JSON."
  value = { for k, sa in google_service_account.this : k => jsonencode({
    type                              = "external_account"
    audience                          = "//iam.googleapis.com/${local.pool_name}/providers/${google_iam_workload_identity_pool_provider.aws.workload_identity_pool_provider_id}"
    subject_token_type                = "urn:ietf:params:aws:token-type:aws4_request"
    service_account_impersonation_url = "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/${sa.email}:generateAccessToken"
    token_url                         = "https://sts.googleapis.com/v1/token"
    credential_source = {
      environment_id                 = "aws1"
      region_url                     = "http://169.254.169.254/latest/meta-data/placement/availability-zone"
      url                            = "http://169.254.169.254/latest/meta-data/iam/security-credentials"
      regional_cred_verification_url = "https://sts.{region}.amazonaws.com?Action=GetCallerIdentity&Version=2011-06-15"
      imdsv2_session_token_url       = "http://169.254.169.254/latest/api/token"
    }
  }) if k != "log-push" }
}
