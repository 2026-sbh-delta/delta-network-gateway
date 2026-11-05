# 공용 LLM 신원 역할. 권한은 AWS 신원 토큰(GetWebIdentityToken) 발급뿐이다.
# Anthropic 쪽 규칙은 이 역할 ARN 하나에만 일치하게 등록한다. LLM을 쓰는 서비스가 늘어도 콘솔 작업은 없다.
# 누가 LLM을 쓸 수 있는지는 이 역할의 신뢰 정책(서비스 역할 목록)으로 정한다.

data "aws_iam_policy_document" "llm_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = [for key, svc in local.services : module.host[key].role_arn if svc.llm]
    }
  }
}

resource "aws_iam_role" "llm" {
  name                 = local.llm_role_name
  assume_role_policy   = data.aws_iam_policy_document.llm_trust.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "llm" {
  statement {
    sid       = "OutboundIdentityFederation"
    actions   = ["sts:GetWebIdentityToken"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "llm" {
  name   = local.llm_role_name
  role   = aws_iam_role.llm.id
  policy = data.aws_iam_policy_document.llm.json
}
