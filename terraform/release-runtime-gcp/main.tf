# 운영 배포(환경별 VM·PostgreSQL 18) GCP 런타임 기반. 동규님 platform/deploy/workloads/runtime-gcp(Terraform)와
# connect-network.py(스크립트)로 만든 것 중 우리 운영 배포가 쓰는 것만 이 루트로 옮겼다(import, 11/6).
# 배포 계정·앱 계정·권한·수신/송신 방화벽·인터넷 경로·NAT·앱 이미지 저장소.
# 동규님 AWS 배포 VM 전용(VPN 쪽 DB 연결, WIF 풀 delta-deployment-aws, 시연 DB 비밀값 권한)은 옮기지 않는다.
# 우리 운영 배포에서 추가한 권한(새 PG18 서버 생성)·앱 VM 권한·PG18 송신 규칙도 여기 있다. Jenkins WIF 연결만 onboarding-gcp 에 있다.

locals {
  deployer = "serviceAccount:${google_service_account.deployer.email}"
  app      = "serviceAccount:${google_service_account.app.email}"
}

data "google_project" "current" {}

resource "google_service_account" "deployer" {
  account_id   = "delta-platform-deployer"
  display_name = "Delta platform deployer"
}
resource "google_service_account" "app" {
  account_id   = "delta-platform-app"
  display_name = "Delta platform app"
}

# 배포 계정 → 앱 VM 계정으로 VM 을 만든다.
resource "google_service_account_iam_member" "act_as" {
  service_account_id = google_service_account.app.name
  role               = "roles/iam.serviceAccountUser"
  member             = local.deployer
}

resource "google_project_iam_custom_role" "provision" {
  role_id = "deltaPlatformVmProvision"
  title   = "Delta customer VM creation and discovery"
  permissions = [
    "compute.instances.create", "compute.instances.list", "compute.instances.get",
    "compute.disks.create", "compute.disks.get", "compute.images.get", "compute.images.useReadOnly",
    "compute.machineTypes.get", "compute.zones.get", "compute.zoneOperations.get",
    "compute.regionOperations.get", "compute.globalOperations.get", "compute.projects.get",
    "compute.networks.get", "compute.subnetworks.get", "serviceusage.services.use",
  ]
}
resource "google_project_iam_member" "provision" {
  project = var.project_id
  role    = google_project_iam_custom_role.provision.name
  member  = local.deployer
}

resource "google_project_iam_custom_role" "manage" {
  role_id = "deltaPlatformVmManage"
  title   = "Delta customer VM lifecycle"
  permissions = [
    "compute.instances.delete", "compute.instances.setMetadata", "compute.instances.setLabels",
    "compute.instances.setTags", "compute.instances.setServiceAccount", "compute.instances.setShieldedInstanceIntegrityPolicy",
    "compute.instances.osLogin", "compute.instances.osAdminLogin", "compute.disks.delete", "compute.disks.use",
  ]
}
resource "google_project_iam_member" "manage" {
  project = var.project_id
  role    = google_project_iam_custom_role.manage.name
  member  = local.deployer
  condition {
    title      = "platform_customer_resources"
    expression = "resource.name.startsWith('projects/${var.project_id}/zones/${var.zone}/instances/delta-app-') || resource.name.startsWith('projects/${var.project_id}/zones/${var.zone}/disks/delta-app-')"
  }
}

resource "google_project_iam_member" "iap" {
  project = var.project_id
  role    = "roles/iap.tunnelResourceAccessor"
  member  = local.deployer
  condition {
    title      = "ssh_only"
    expression = "destination.port == 22"
  }
}

resource "google_compute_subnetwork_iam_member" "use" {
  subnetwork = var.subnet_name
  region     = var.region
  role       = "roles/compute.networkUser"
  member     = local.deployer
}

resource "google_project_iam_custom_role" "secret_create" {
  role_id     = "deltaPlatformDbSecretCreate"
  title       = "Create customer database credential secrets"
  permissions = ["secretmanager.secrets.create"]
}
resource "google_project_iam_member" "secret_create" {
  project = var.project_id
  role    = google_project_iam_custom_role.secret_create.name
  member  = local.deployer
}
resource "google_project_iam_custom_role" "secret_manage" {
  role_id     = "deltaPlatformDbSecretManage"
  title       = "Manage platform customer database credential secrets"
  permissions = ["secretmanager.secrets.get", "secretmanager.secrets.getIamPolicy", "secretmanager.secrets.setIamPolicy", "secretmanager.versions.add"]
}
resource "google_project_iam_member" "secret_manage" {
  project = var.project_id
  role    = google_project_iam_custom_role.secret_manage.name
  member  = local.deployer
  condition {
    title      = "platform_db_secrets"
    expression = "resource.name.startsWith('projects/${data.google_project.current.number}/secrets/delta-db-')"
  }
}

# 동규님 플랫폼의 앱 이미지 저장소(우리 배포는 스테이징 AR 의 빌드 묶음을 그대로 쓴다).
resource "google_artifact_registry_repository" "app" {
  location      = var.region
  repository_id = "delta-platform-apps"
  format        = "DOCKER"
}
resource "google_artifact_registry_repository_iam_member" "access" {
  for_each   = { deployer = "roles/artifactregistry.writer", app = "roles/artifactregistry.reader" }
  location   = var.region
  repository = google_artifact_registry_repository.app.name
  role       = each.value
  member     = each.key == "deployer" ? local.deployer : local.app
}

# 앱 VM 방화벽(대상: 앱 VM 계정). 기존 네트워크 방화벽 정책보다 먼저 적용되는 일반 규칙이다.
locals {
  ingress = {
    iap      = { ranges = ["35.235.240.0/20"], ports = ["22"] }
    deployer = { ranges = [var.legacy_deployer_cidr], ports = ["8088"] }
    lb       = { ranges = ["130.211.0.0/22", "35.191.0.0/16", var.proxy_cidr], ports = ["8088"] }
  }
}
resource "google_compute_firewall" "ingress" {
  for_each                = local.ingress
  name                    = "delta-platform-app-${each.key}"
  network                 = var.network_name
  direction               = "INGRESS"
  priority                = 100
  source_ranges           = each.value.ranges
  target_service_accounts = [google_service_account.app.email]
  allow {
    protocol = "tcp"
    ports    = each.value.ports
  }
  log_config { metadata = "EXCLUDE_ALL_METADATA" }
}
resource "google_compute_firewall" "database" {
  name                    = "delta-platform-app-database"
  network                 = var.network_name
  direction               = "EGRESS"
  priority                = 100
  destination_ranges      = ["${var.legacy_database_address}/32"]
  target_service_accounts = [google_service_account.app.email]
  allow {
    protocol = "tcp"
    ports    = ["5432"]
  }
  log_config { metadata = "EXCLUDE_ALL_METADATA" }
}
# 송신: HTTPS(이미지 pull·비밀값·로그)만 열고 나머지는 막는다(앞의 5432 규칙들이 먼저 적용된다).
resource "google_compute_firewall" "https" {
  name                    = "delta-platform-app-https"
  network                 = var.network_name
  direction               = "EGRESS"
  priority                = 100
  destination_ranges      = ["0.0.0.0/0"]
  target_service_accounts = [google_service_account.app.email]
  allow {
    protocol = "tcp"
    ports    = ["443"]
  }
  log_config { metadata = "EXCLUDE_ALL_METADATA" }
}
resource "google_compute_firewall" "egress_deny" {
  name                    = "delta-platform-app-egress-deny"
  network                 = var.network_name
  direction               = "EGRESS"
  priority                = 1000
  destination_ranges      = ["0.0.0.0/0"]
  target_service_accounts = [google_service_account.app.email]
  deny {
    protocol = "all"
  }
  log_config { metadata = "EXCLUDE_ALL_METADATA" }
}

# 워크로드 VPC 는 기본 인터넷 경로가 없다. 운영 앱 VM 태그에만 인터넷 경로를 주고 NAT 로 나간다.
resource "google_compute_route" "egress" {
  name             = "delta-platform-app-egress"
  network          = var.network_name
  dest_range       = "0.0.0.0/0"
  next_hop_gateway = "default-internet-gateway"
  priority         = 2000
  tags             = [var.network_tag]
}
resource "google_compute_router" "egress" {
  name    = "delta-platform-app-egress"
  region  = var.region
  network = var.network_name
}
resource "google_compute_router_nat" "egress" {
  name                               = "delta-platform-app-egress"
  router                             = google_compute_router.egress.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"
  subnetwork {
    name                    = "projects/${var.project_id}/regions/${var.region}/subnetworks/${var.subnet_name}"
    source_ip_ranges_to_nat = ["PRIMARY_IP_RANGE"]
  }
  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

# 운영 배포의 새 PostgreSQL 18 서버(Cloud SQL·PSC 사설 주소·사설 DNS·관리자 비밀값)를 만드는 권한.
resource "google_project_iam_member" "release_deployer" {
  for_each = toset(["roles/cloudsql.admin", "roles/dns.admin", "roles/compute.networkAdmin", "roles/secretmanager.admin"])
  project  = var.project_id
  role     = each.value
  member   = local.deployer
}

# 앱 VM 계정: 우리 빌드 묶음(스테이징 AR) 이미지 pull, 컨테이너 로그(gcplogs) 쓰기.
resource "google_artifact_registry_repository_iam_member" "release_app_pull" {
  location   = var.region
  repository = var.build_repository
  role       = "roles/artifactregistry.reader"
  member     = local.app
}
resource "google_project_iam_member" "release_app_logs" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = local.app
}

# 앱 VM → 같은 앱 서브넷의 PostgreSQL 사설 연결(PSC) 주소. 송신 차단(1000)보다 먼저 적용한다. 5432 만 연다.
resource "google_compute_firewall" "release_app_database" {
  name                    = "delta-cp-release-app-database"
  network                 = var.network_name
  direction               = "EGRESS"
  priority                = 100
  destination_ranges      = [var.app_subnet_cidr]
  target_service_accounts = [google_service_account.app.email]
  allow {
    protocol = "tcp"
    ports    = ["5432"]
  }
}
