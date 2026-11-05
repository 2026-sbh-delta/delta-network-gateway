# A 기업 AWS 쪽 온보딩: 플랫폼 역할이 ExternalId를 붙여서만 위임받을 수 있는 역할 4개.
# 현실에서는 A 기업이 한 번 적용한다. A 기업 계정의 인프라를 바꾸는 권한은 주지 않는다(로그 전송 설정만 예외).

locals {
  p = "arn:aws:iam::${var.platform_account_id}:role/${var.platform_role_prefix}"
  a = var.target_account_id
  r = var.region
  roles = {
    staging-deploy = { trusted = "${local.p}jenkins", policy = data.aws_iam_policy_document.staging.json }
    prod-deploy    = { trusted = "${local.p}jenkins", policy = data.aws_iam_policy_document.prod.json }
    log-setup      = { trusted = "${local.p}jenkins", policy = data.aws_iam_policy_document.log_setup.json }
    readonly       = { trusted = "${local.p}diff", policy = data.aws_iam_policy_document.readonly.json }
  }
}

# 신뢰 대상을 역할 ARN 이 아니라 플랫폼 계정 + aws:PrincipalArn 조건으로 둔다.
# 역할 ARN 을 principal 로 두면 플랫폼 역할을 지웠다 다시 만들 때(야간 정리 후 재생성) 신뢰 정책에 옛 고유 ID 가 남아
# 위임이 끊긴다(11/5 실측: delta-readonly 가 삭제된 delta-cp-diff 의 ID 를 가리킴). 조건은 같은 역할 이름만 허용한다.
data "aws_iam_policy_document" "trust" {
  for_each = local.roles
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${var.platform_account_id}:root"]
    }
    condition {
      test     = "ArnEquals"
      variable = "aws:PrincipalArn"
      values   = [each.value.trusted]
    }
    condition {
      test     = "StringEquals"
      variable = "sts:ExternalId"
      values   = [var.external_id]
    }
  }
}

resource "aws_iam_role" "this" {
  for_each             = local.roles
  name                 = "delta-${each.key}"
  assume_role_policy   = data.aws_iam_policy_document.trust[each.key].json
  max_session_duration = 3600
}

resource "aws_iam_role_policy" "this" {
  for_each = local.roles
  name     = "delta-${each.key}"
  role     = aws_iam_role.this[each.key].id
  policy   = each.value.policy
}

resource "aws_iam_role_policy_attachment" "readonly" {
  for_each   = toset(["arn:aws:iam::aws:policy/SecurityAudit", "arn:aws:iam::aws:policy/job-function/ViewOnlyAccess"])
  role       = aws_iam_role.this["readonly"].name
  policy_arn = each.value
}
