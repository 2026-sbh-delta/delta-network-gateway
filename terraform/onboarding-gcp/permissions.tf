# 서비스 계정별 권한. 지금은 프로젝트 단위로 주고, 범위를 좁히는 일은 다음 단계에서 한다(README 참고).

locals {
  project_roles = {
    # 읽기 2개: 초기화 Job 결과(남은 행 0) 로그 확인, 실행 이미지 digest 확인.
    "staging-deploy" = ["roles/run.developer", "custom/scanner_access", "roles/logging.viewer", "roles/artifactregistry.reader"]
    "prod-deploy"    = ["roles/run.developer"]
    "infra" = [
      "roles/compute.loadBalancerAdmin",
      "roles/compute.securityAdmin",
      "roles/logging.configWriter",
      "roles/pubsub.editor",
      "roles/securitycenter.notificationConfigEditor",
    ]
    "readonly" = [
      "roles/iam.securityReviewer",
      "roles/cloudasset.viewer",
      "roles/securitycenter.adminViewer",
      "roles/compute.viewer",
      "roles/run.viewer",
    ]
  }
  # 키는 정적으로 두고(사용자 지정 역할 이름은 적용 뒤에 정해진다), 값에서 실제 역할 이름으로 바꾼다.
  custom_roles   = { "custom/scanner_access" = google_project_iam_custom_role.scanner_access.name }
  project_grants = merge([for sa, roles in local.project_roles : { for r in roles : "${sa}/${r}" => { sa = sa, role = lookup(local.custom_roles, r, r) } }]...)
}

# 스테이징 도구가 Cloud Armor에 스캐너 IP를 넣고 빼고, LB 주소를 조회하는 데 필요한 것만.
resource "google_project_iam_custom_role" "scanner_access" {
  role_id     = "deltaStagingScannerAccess"
  title       = "Delta staging scanner access"
  description = "Cloud Armor 정책 조회·갱신, 전역 작업·주소 조회"
  permissions = [
    "compute.securityPolicies.get",
    "compute.securityPolicies.update",
    "compute.globalOperations.get",
    "compute.globalAddresses.get",
  ]
}

resource "google_project_iam_member" "grant" {
  for_each = local.project_grants
  project  = var.project_id
  role     = each.value.role
  member   = "serviceAccount:${google_service_account.this[each.value.sa].email}"
}

# 스테이징 이미지 저장소에만 푸시(태그 조회 포함).
resource "google_artifact_registry_repository_iam_member" "staging_writer" {
  location   = var.region
  repository = "${var.staging_name}-staging" # terraform/staging-gcp의 저장소 ID(local.prefix)
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.this["staging-deploy"].email}"
}

resource "google_artifact_registry_repository_iam_member" "build_push_writer" {
  location   = var.region
  repository = "${var.staging_name}-staging"
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.this["build-push"].email}"
}

# 스테이징 Cloud Run 런타임 계정으로만 배포할 수 있다(actAs는 이 계정 하나).
resource "google_service_account_iam_member" "staging_act_as" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${var.staging_name}-stg-run@${var.project_id}.iam.gserviceaccount.com"
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.this["staging-deploy"].email}"
}

# Terraform state: 차분 에이전트는 읽기, 인프라 적용은 쓰기.
resource "google_storage_bucket_iam_member" "state_read" {
  bucket = var.tfstate_bucket
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.this["readonly"].email}"
}

resource "google_storage_bucket_iam_member" "state_write" {
  bucket = var.tfstate_bucket
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.this["infra"].email}"
}
