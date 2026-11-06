# A 기업 GCP 쪽 온보딩: 플랫폼(AWS)의 역할을 키 없이 GCP 서비스 계정으로 바꿔 주는 WIF 풀과, 용도별 서비스 계정.
# 현실에서는 A 기업이 한 번 적용한다. 서비스 계정 키는 만들지 않는다.

data "google_project" "this" {}

locals {
  pool_name = "projects/${data.google_project.this.number}/locations/global/workloadIdentityPools/${google_iam_workload_identity_pool.platform.workload_identity_pool_id}"
  # 플랫폼 역할 이름 → 이 역할로 쓸 수 있는 서비스 계정
  bindings = {
    "staging-deploy" = ["${var.platform_role_prefix}jenkins"]
    "prod-deploy"    = ["${var.platform_role_prefix}jenkins"]
    "infra"          = ["${var.platform_role_prefix}jenkins"]
    "readonly"       = ["${var.platform_role_prefix}diff"]
    # 빌드 에이전트: 앱 코드를 빌드해 스테이징 이미지 저장소에 푸시만 한다(배포 권한 없음).
    "build-push" = ["${var.platform_role_prefix}jenkins-agent"]
  }
  sa_bindings = merge([for sa, roles in local.bindings : { for r in roles : "${sa}/${r}" => { sa = sa, role = r } }]...)
}

resource "google_project_service" "needed" {
  for_each           = toset(["iam.googleapis.com", "iamcredentials.googleapis.com", "sts.googleapis.com", "cloudresourcemanager.googleapis.com"])
  service            = each.value
  disable_on_destroy = false
}

resource "google_iam_workload_identity_pool" "platform" {
  workload_identity_pool_id = "delta-platform"
  display_name              = "Delta platform (AWS)"
  description               = "플랫폼 AWS 계정의 역할만 받는다"
  depends_on                = [google_project_service.needed]
}

resource "google_iam_workload_identity_pool_provider" "aws" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.platform.workload_identity_pool_id
  workload_identity_pool_provider_id = "platform-aws"
  display_name                       = "Platform AWS account"
  aws {
    account_id = var.platform_aws_account_id
  }
  # 역할 이름만 꺼내 속성으로 쓴다(세션 이름은 인스턴스마다 다르다).
  attribute_mapping = {
    "google.subject"     = "assertion.arn"
    "attribute.aws_role" = "assertion.arn.extract('assumed-role/{role}/')"
  }
  # 플랫폼 계정의 delta-cp-* 역할로 위임받은 세션만 받는다.
  attribute_condition = "assertion.arn.startsWith('arn:aws:sts::${var.platform_aws_account_id}:assumed-role/${var.platform_role_prefix}')"
}

resource "google_service_account" "this" {
  for_each     = toset(["staging-deploy", "prod-deploy", "infra", "readonly", "log-push", "build-push"])
  account_id   = "delta-${each.key}"
  display_name = "Delta platform: ${each.key}"
}

# 플랫폼 역할 → 서비스 계정 사용 허용. 이 묶음 밖의 역할은 같은 풀에 들어와도 아무 계정도 못 쓴다.
resource "google_service_account_iam_member" "wif" {
  for_each           = local.sa_bindings
  service_account_id = google_service_account.this[each.value.sa].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${local.pool_name}/attribute.aws_role/${each.value.role}"
}

# 운영 배포(동규님 방식, 환경별 VM 생성)는 기존 배포 계정 delta-platform-deployer 로 한다.
# 이 계정의 VM 생성·IAP·DB 비밀값 권한은 platform/deploy/workloads/runtime-gcp 가 만든다(Terraform 통일 때 이 루트로 옮긴다).
# 여기서는 플랫폼 Jenkins 역할만 이 계정을 쓰도록 허용한다.
resource "google_service_account_iam_member" "release_deployer" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${var.release_deployer_account}"
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${local.pool_name}/attribute.aws_role/${var.platform_role_prefix}jenkins"
}

# 운영 배포 런타임 권한·방화벽은 terraform/release-runtime-gcp 로 옮겼다(11/6). 리소스는 지우지 않고 state 소유만 넘긴다.
removed {
  from = google_project_iam_member.release_deployer
  lifecycle { destroy = false }
}
removed {
  from = google_artifact_registry_repository_iam_member.release_app_pull
  lifecycle { destroy = false }
}
removed {
  from = google_project_iam_member.release_app_logs
  lifecycle { destroy = false }
}
removed {
  from = google_compute_firewall.release_app_database
  lifecycle { destroy = false }
}
