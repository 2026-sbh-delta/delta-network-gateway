locals {
  prefix = var.name
  common_tags = merge({
    Project   = "delta"
    Component = "control-plane"
    ManagedBy = "terraform"
  }, var.tags)

  # 10.90.0.0/24, 10.90.1.0/24: 퍼블릭(NAT). 10.90.2.0/24, 10.90.3.0/24: 내부 ALB와 CloudFront VPC Origin ENI 전용.
  # 10.90.10.0/24: 서비스 인스턴스.
  public_cidrs  = [cidrsubnet(var.vpc_cidr, 8, 0), cidrsubnet(var.vpc_cidr, 8, 1)]
  alb_cidrs     = [cidrsubnet(var.vpc_cidr, 8, 2), cidrsubnet(var.vpc_cidr, 8, 3)]
  service_cidr  = cidrsubnet(var.vpc_cidr, 8, 10)
  internal_zone = "cp.internal"

  # 서비스별 인스턴스. 파라미터 경로: /<prefix>/<service>/ 와 공통 /<prefix>/shared/
  # port 0 = 받는 연결 없음. Jenkins만 x86(스테이징·운영과 같은 아키텍처로 빌드), 나머지는 Graviton.
  services = {
    orchestrator  = { port = 8080, instance_type = var.small_instance_type, volume_gb = 20, llm = true, arch = "arm64" }
    jenkins       = { port = 8080, instance_type = var.jenkins_instance_type, volume_gb = 40, llm = false, arch = "x86_64" }
    jenkins-agent = { port = 0, instance_type = var.jenkins_instance_type, volume_gb = 60, llm = false, arch = "x86_64" }
    sast          = { port = 8790, instance_type = var.sast_instance_type, volume_gb = 30, llm = true, arch = "arm64" }
    dast          = { port = 8765, instance_type = var.dast_instance_type, volume_gb = 40, llm = true, arch = "arm64" }
    diff          = { port = 8095, instance_type = var.small_instance_type, volume_gb = 20, llm = true, arch = "arm64" }
    log           = { port = 8099, instance_type = var.small_instance_type, volume_gb = 30, llm = false, arch = "arm64" }
  }

  # 서비스 사이 연결. 여기 적은 것만 열린다(그 밖은 모두 차단).
  links = [
    { from = "orchestrator", to = "jenkins", port = 8080 },
    { from = "orchestrator", to = "sast", port = 8790 },
    { from = "orchestrator", to = "dast", port = 8765 },
    { from = "orchestrator", to = "diff", port = 8095 },
    { from = "jenkins-agent", to = "jenkins", port = 8080 },
    { from = "jenkins", to = "sast", port = 8790 },
    { from = "dast", to = "sast", port = 8790 },
    { from = "dast", to = "jenkins", port = 8091 },         # 스테이징 도구(주소 조회)
    { from = "orchestrator", to = "jenkins", port = 8091 }, # 스테이징 도구(배포 상태)
    { from = "orchestrator", to = "log", port = 8099 },
    { from = "jenkins", to = "log", port = 8099 },
    { from = "sast", to = "log", port = 8099 },
    { from = "dast", to = "log", port = 8099 },
    { from = "diff", to = "log", port = 8099 },
  ]
  link_map = { for l in local.links : "${l.from}>${l.to}:${l.port}" => l }

  # LLM을 쓰는 서비스는 이 역할 하나를 위임받아 Anthropic OIDC 토큰을 받는다. 규칙은 이 ARN 하나만 등록한다.
  llm_role_name = "${local.prefix}-llm"
  llm_role_arn  = "arn:aws:iam::${var.account_id}:role/${local.llm_role_name}"
  # CloudFront로 공개하는 서비스. 헤더 X-Delta-App 값으로 ALB가 대상 그룹을 고른다.
  # paths가 있으면 그 경로만 받는다(Jenkins는 GitHub 웹훅, 로그는 수집 API만 공개).
  public_apps = {
    orchestrator = { priority = 5, health_path = "/healthz", paths = [] }
    dast         = { priority = 10, health_path = "/healthz", paths = [] }
    jenkins      = { priority = 20, health_path = "/login", paths = ["/github-webhook/*"] }
    log          = { priority = 30, health_path = "/healthz", paths = ["/v1/ingest/*"] }
  }
}

data "aws_ssm_parameter" "al2023" {
  for_each = toset(["arm64", "x86_64"])
  name     = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-${each.key}"
}
