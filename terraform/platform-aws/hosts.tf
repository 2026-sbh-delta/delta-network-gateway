# 서비스마다 보안 그룹 하나. 규칙은 security.tf에서 필요한 연결만 붙인다(기본 송신 규칙 없음).
resource "aws_security_group" "host" {
  for_each    = local.services
  name        = "${local.prefix}-${each.key}"
  description = "${each.key} host"
  vpc_id      = aws_vpc.main.id
  tags        = { Name = "${local.prefix}-${each.key}" }
}

moved {
  from = module.host["sast"].aws_security_group.this
  to   = aws_security_group.host["sast"]
}

moved {
  from = module.host["dast"].aws_security_group.this
  to   = aws_security_group.host["dast"]
}

module "host" {
  source   = "./modules/service-host"
  for_each = local.services

  name                  = "${local.prefix}-${each.key}"
  service               = each.key
  region                = var.region
  account_id            = var.account_id
  security_group_id     = aws_security_group.host[each.key].id
  subnet_id             = aws_subnet.service.id
  ami_id                = data.aws_ssm_parameter.al2023[each.value.arch].value
  instance_type         = each.value.instance_type
  root_volume_gb        = each.value.volume_gb
  parameter_paths       = ["/${local.prefix}/${each.key}/", "/${local.prefix}/shared/"]
  llm_role_arn          = each.value.llm ? local.llm_role_arn : null
  extra_policy_json     = lookup(local.extra_policies, each.key, null)
  extra_parameter_names = [for n in lookup(local.extra_parameters, each.key, []) : "/${local.prefix}/${n}"]
  spot                  = contains(var.spot_services, each.key)
  user_data             = templatefile("${path.module}/templates/user_data.sh.tftpl", { service = each.key })
  log_group_name        = "/${local.prefix}/platform/${each.key}"

  # 송신 규칙과 NAT 경로가 생긴 뒤에 부팅해야 SSM 에이전트가 첫 등록에 성공한다.
  depends_on = [
    aws_route_table_association.service,
    aws_nat_gateway.main,
    aws_vpc_security_group_egress_rule.https_out,
    aws_vpc_security_group_egress_rule.http_out,
    aws_vpc_security_group_egress_rule.link_out,
  ]
}

# 서비스에만 필요한 추가 권한.
locals {
  # 자기 경로 밖에서 읽는 파라미터(정확한 이름만). 그 밖은 모듈의 명시적 거부로 막힌다.
  extra_parameters = {
    # 오케스트레이터: Jenkins 'platform' 사용자(읽기·빌드만) 비밀번호, 스테이징 조회 토큰, DAST 플랫폼 토큰
    # 운영 대상 인벤토리(비밀 아님)는 Jenkins 와 같은 값을 쓴다
    orchestrator = ["jenkins/platform_password", "jenkins/staging_read_token", "dast/platform_token", "jenkins/release_targets"]
    # DAST: 스테이징 조회 토큰 하나(변경 토큰·Jenkins 경로 전체는 못 읽음)
    dast = ["jenkins/staging_read_token"]
    # 대시보드: 오케스트레이터 관리자 로그인(실행 기록 조회·승인 전달 중계)
    dashboard = ["orchestrator/admin_password"]
  }

  extra_policies = {
    # 컨트롤러가 빌드 에이전트 접속 비밀값을 만들어 에이전트 경로에 넣는다(그 파라미터 하나만).
    jenkins = jsonencode({
      Version = "2012-10-17"
      Statement = [{
        Sid      = "WriteAgentSecret"
        Effect   = "Allow"
        Action   = ["ssm:PutParameter"]
        Resource = "arn:aws:ssm:${var.region}:${var.account_id}:parameter/${local.prefix}/jenkins-agent/agent_secret"
        }, {
        # 대상(A 기업) 계정의 배포·로그 설정 역할만 위임받는다. 대상 쪽 신뢰 정책이 ExternalId를 요구한다.
        Sid    = "AssumeTargetDeployRoles"
        Effect = "Allow"
        Action = ["sts:AssumeRole"]
        Resource = [for r in ["delta-staging-deploy", "delta-prod-deploy", "delta-log-setup"] :
        "arn:aws:iam::${var.target_aws_account_id}:role/${r}"]
        }, {
        # 운영 배포(환경별 VM·DB)의 Terraform state: 정해진 접두사만 읽고 쓴다(잠금 파일 포함).
        Sid       = "ReleaseStateList"
        Effect    = "Allow"
        Action    = ["s3:ListBucket"]
        Resource  = "arn:aws:s3:::${var.release_state_bucket}"
        Condition = { StringLike = { "s3:prefix" = ["delta-cp/release/*"] } }
        }, {
        Sid      = "ReleaseStateObjects"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = "arn:aws:s3:::${var.release_state_bucket}/delta-cp/release/*"
      }]
    })
    # 빌드 에이전트: 같은 manifest 를 A 기업 ECR(스테이징 저장소)에 복사한다(AWS 스테이징·운영이 같은 digest 를 쓴다).
    jenkins-agent = jsonencode({
      Version = "2012-10-17"
      Statement = [{
        Sid      = "EcrAuth"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
        }, {
        Sid    = "PushBundleCopy"
        Effect = "Allow"
        Action = ["ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
        "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage", "ecr:DescribeImages"]
        Resource = "arn:aws:ecr:${var.region}:${var.target_aws_account_id}:repository/${var.bundle_ecr_repository}"
      }]
    })
    diff = jsonencode({
      Version = "2012-10-17"
      Statement = [{
        Sid      = "AssumeTargetReadonly"
        Effect   = "Allow"
        Action   = ["sts:AssumeRole"]
        Resource = "arn:aws:iam::${var.target_aws_account_id}:role/delta-readonly"
      }]
    })
  }
}
