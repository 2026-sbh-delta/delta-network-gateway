# 스테이징 배포: 스테이징 도구 README의 권한 목록. 이름 접두사로 스테이징 자원에만 묶는다.
data "aws_iam_policy_document" "staging" {
  statement {
    sid       = "EcsRead"
    actions   = ["ecs:DescribeTaskDefinition", "ecs:RegisterTaskDefinition", "ecs:ListTasks", "ecs:DescribeTasks", "ecs:TagResource"]
    resources = ["*"]
  }
  statement {
    sid       = "EcsUpdateStagingService"
    actions   = ["ecs:UpdateService", "ecs:DescribeServices"]
    resources = ["arn:aws:ecs:${local.r}:${local.a}:service/${var.staging_prefix}/*"]
  }
  # 배포 전 스테이징 DB 초기화: 초기화 전용 task family만 실행하고, 그 출력(남은 행 0)만 읽는다.
  statement {
    sid       = "RunStagingDbReset"
    actions   = ["ecs:RunTask"]
    resources = ["arn:aws:ecs:${local.r}:${local.a}:task-definition/${var.staging_prefix}-db-reset:*"]
  }
  statement {
    sid       = "ReadStagingDbResetOutput"
    actions   = ["logs:GetLogEvents"]
    resources = ["arn:aws:logs:${local.r}:${local.a}:log-group:/delta/${var.staging_prefix}:log-stream:reset/*"]
  }
  statement {
    sid       = "ElbRead"
    actions   = ["elasticloadbalancing:DescribeTargetHealth", "elasticloadbalancing:DescribeTargetGroups", "elasticloadbalancing:DescribeLoadBalancers"]
    resources = ["*"]
  }
  statement {
    sid       = "PassStagingTaskRoles"
    actions   = ["iam:PassRole"]
    resources = ["arn:aws:iam::${local.a}:role/${var.staging_prefix}-*"]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
  statement {
    sid       = "ScannerRuleRead"
    actions   = ["ec2:DescribeSecurityGroupRules", "ec2:DescribeSecurityGroups"]
    resources = ["*"]
  }
  # 스캐너 IP 규칙은 스테이징 보안 그룹(태그 Component=staging)에만 넣고 뺀다.
  statement {
    sid       = "ScannerRuleWrite"
    actions   = ["ec2:AuthorizeSecurityGroupIngress", "ec2:RevokeSecurityGroupIngress", "ec2:CreateTags"]
    resources = ["arn:aws:ec2:${local.r}:${local.a}:security-group/*", "arn:aws:ec2:${local.r}:${local.a}:security-group-rule/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/Component"
      values   = ["staging"]
    }
  }
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid = "PushStagingImage"
    actions = ["ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
    "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage", "ecr:DescribeImages"]
    resources = ["arn:aws:ecr:${local.r}:${local.a}:repository/${var.staging_prefix}*"]
  }
}

# 운영 배포: 서비스 갱신만. 보안 그룹 변경·이미지 푸시는 없다(스테이징에서 검증한 digest만 참조).
# 운영 = VM + Docker Compose(11/5 결정). 지정 VM의 SSH(22) 터널과 일회용 키 등록만. 이미지 pull은 VM 자신의 역할이 한다.
data "aws_iam_policy_document" "prod" {
  dynamic "statement" {
    for_each = var.prod_hosts
    content {
      sid       = "SendKey${replace(title(replace(statement.key, "-", " ")), " ", "")}"
      actions   = ["ec2-instance-connect:SendSSHPublicKey"]
      resources = ["arn:aws:ec2:${local.r}:${local.a}:instance/${statement.value.instance_id}"]
      condition {
        test     = "StringEquals"
        variable = "ec2:osuser"
        values   = [statement.value.os_user]
      }
    }
  }
  dynamic "statement" {
    for_each = var.prod_hosts
    content {
      sid       = "Tunnel${replace(title(replace(statement.key, "-", " ")), " ", "")}"
      actions   = ["ec2-instance-connect:OpenTunnel"]
      resources = ["arn:aws:ec2:${local.r}:${local.a}:instance-connect-endpoint/${statement.value.endpoint_id}"]
      condition {
        test     = "NumericEquals"
        variable = "ec2-instance-connect:remotePort"
        values   = ["22"]
      }
      condition {
        test     = "IpAddress"
        variable = "ec2-instance-connect:privateIpAddress"
        values   = ["${statement.value.private_ip}/32"]
      }
      condition {
        test     = "NumericLessThanEquals"
        variable = "ec2-instance-connect:maxTunnelDuration"
        values   = ["900"]
      }
    }
  }
  statement {
    sid       = "Describe"
    actions   = ["ec2:DescribeInstances", "ec2:DescribeInstanceConnectEndpoints"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [local.r]
    }
  }
}

# 로그 전송 설정: EventBridge·Firehose와 WAF·Network Firewall 로깅만.
data "aws_iam_policy_document" "log_setup" {
  statement {
    sid = "EventBridge"
    actions = ["events:PutRule", "events:PutTargets", "events:RemoveTargets", "events:DeleteRule", "events:Describe*", "events:List*",
    "events:CreateConnection", "events:UpdateConnection", "events:CreateApiDestination", "events:UpdateApiDestination"]
    resources = ["*"]
  }
  statement {
    sid       = "Firehose"
    actions   = ["firehose:CreateDeliveryStream", "firehose:UpdateDestination", "firehose:Describe*", "firehose:List*"]
    resources = ["arn:aws:firehose:${local.r}:${local.a}:deliverystream/delta-*"]
  }
  statement {
    sid       = "Logging"
    actions   = ["wafv2:PutLoggingConfiguration", "wafv2:GetLoggingConfiguration", "network-firewall:UpdateLoggingConfiguration", "network-firewall:DescribeLoggingConfiguration"]
    resources = ["*"]
  }
  statement {
    sid       = "PassLogRoles"
    actions   = ["iam:PassRole"]
    resources = ["arn:aws:iam::${local.a}:role/delta-log-*"]
  }
}

# 차분 에이전트: 설정 조회(관리형 정책) + A 기업 Terraform state 읽기. 쓰기·잠금 없음.
# 차분 에이전트 입력: A 기업 Terraform state 읽기만. 플랫폼 자신의 state(platform-aws 등)는 넣지 않는다.
locals {
  readonly_prefixes = distinct(concat(var.tfstate_prefixes, var.app_tfstate_prefixes))
}

data "aws_iam_policy_document" "readonly" {
  statement {
    sid       = "ListStatePrefixes"
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::${var.tfstate_bucket}"]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = [for p in local.readonly_prefixes : "${p}*"]
    }
  }
  statement {
    sid       = "ReadState"
    actions   = ["s3:GetObject"]
    resources = [for p in local.readonly_prefixes : "arn:aws:s3:::${var.tfstate_bucket}/${p}*"]
  }
  # 중앙 네트워크·보안 state(호원님 관리, 11/5 공유 정책): TGW·ANF·WAF 등 이관 대응 근거
  statement {
    sid       = "ListSecurityBucket"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = ["arn:aws:s3:::${var.central_tfstate_bucket}"]
  }
  statement {
    sid       = "ReadSecurityBucketObjects"
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::${var.central_tfstate_bucket}/*"]
  }
}
