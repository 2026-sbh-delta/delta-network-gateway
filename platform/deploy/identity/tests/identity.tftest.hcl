mock_provider "aws" {
  mock_data "aws_iam_openid_connect_provider" {
    defaults = { client_id_list = ["sts.amazonaws.com"] }
  }
}
variables {
  create_oidc_provider      = false
  account_id                = "111122223333"
  github_subject_prefix     = "repo:example@123/platform@456"
  state_bucket              = "test-platform-state"
  origin_request_policy_id  = "00000000-0000-0000-0000-000000000001"
  cloudfront_prefix_list_id = "pl-00000000000000001"
  platform_vpc_id           = "vpc-00000000000000001"
  platform_subnet_ids       = ["subnet-00000000000000001", "subnet-00000000000000002"]
}
run "exact_main_trust_and_existing_provider" {
  command = plan
  assert {
    condition = alltrue([for role in aws_iam_role.github :
      jsondecode(role.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == "repo:example@123/platform@456:ref:refs/heads/main" &&
      jsondecode(role.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com"
    ])
    error_message = "main의 정확한 immutable subject와 AWS audience만 신뢰해야 합니다."
  }
  assert {
    condition     = length(aws_iam_openid_connect_provider.github) == 0 && length(aws_iam_role.github) == 3
    error_message = "기존 provider를 재사용하고 세 역할을 분리해야 합니다."
  }
}
run "plan_cannot_modify_state_or_resources" {
  command = plan
  assert {
    condition = alltrue([for s in local.plan_policy.Statement :
      alltrue([for action in s.Action : startswith(action, "ec2:Describe") || action == "ec2:GetManagedPrefixListEntries" || startswith(action, "iam:Get") || startswith(action, "iam:List") || startswith(action, "s3:Get") || action == "s3:ListBucket" || startswith(action, "ecr:Describe") || startswith(action, "ecr:List") || startswith(action, "ecr:Get") || startswith(action, "cloudfront:Get") || startswith(action, "cloudfront:List") || s.Sid == "StateLock"])
    ])
    error_message = "Plan의 쓰기 권한은 state 잠금에만 허용합니다."
  }
  assert {
    condition     = [for s in local.plan_policy.Statement : s.Resource if s.Sid == "StateLock"][0] == "arn:aws:s3:::test-platform-state/platform/terraform.tfstate.tflock" && !strcontains(aws_iam_policy.plan.policy, "identity.tfstate")
    error_message = "잠금 경로를 고정하고 identity state는 읽지 못해야 합니다."
  }
}
run "cloudfront_entries_read_is_scoped" {
  command = plan
  assert {
    condition = anytrue([for s in local.plan_policy.Statement :
      contains(s.Action, "ec2:GetManagedPrefixListEntries") && try(s.Resource == "arn:aws:ec2:ap-northeast-2:aws:prefix-list/pl-00000000000000001", false)
    ])
    error_message = "Terraform의 CloudFront data source가 지정 AWS 관리 prefix list의 항목을 읽을 수 있어야 합니다."
  }
}
run "release_is_scoped_and_has_no_secret_api" {
  command = plan
  assert {
    condition     = alltrue([for s in local.release_policy.Statement : alltrue([for action in s.Action : !contains(["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath", "ssm:PutParameter", "iam:PassRole", "s3:DeleteObject"], action)])])
    error_message = "Release는 비밀값·IAM·state 삭제 API를 허용하지 않습니다."
  }
  assert {
    condition = (
      toset([for s in local.release_policy.Statement : s.Condition.StringEquals["ssm:resourceTag/Name"] if s.Sid == "RunOnlyOnPlatformApp"][0]) == toset(["delta-platform-app", "delta-platform-jenkins-controller", "delta-platform-jenkins-agent"]) &&
      [for s in local.release_policy.Statement : s.Resource if s.Sid == "ReleaseArchive"][0] == "arn:aws:s3:::delta-platform-artifacts-111122223333/releases/*"
    )
    error_message = "앱/Jenkins VM과 릴리스 경로만 대상으로 허용해야 합니다."
  }
}
run "apply_cannot_manage_iam_or_ci_identity" {
  command = plan
  assert {
    condition     = alltrue([for s in local.apply_policy.Statement : alltrue([for action in s.Action : !startswith(action, "iam:") || action == "iam:PassRole"])])
    error_message = "일반 Apply에서 IAM 정책과 신뢰를 수정하면 안 됩니다."
  }
  assert {
    condition     = toset([for s in local.apply_policy.Statement : s.Resource if s.Sid == "PassOnlyVMRoles"][0]) == toset(["arn:aws:iam::111122223333:role/delta-platform-app", "arn:aws:iam::111122223333:role/delta-platform-nat", "arn:aws:iam::111122223333:role/delta-platform-jenkins-controller", "arn:aws:iam::111122223333:role/delta-platform-jenkins-agent"])
    error_message = "PassRole은 플랫폼의 지정한 플랫폼/Jenkins VM 역할 네 개로 제한해야 합니다."
  }
}
run "create_provider_only_when_requested" {
  command = plan
  variables { create_oidc_provider = true }
  assert {
    condition     = length(aws_iam_openid_connect_provider.github) == 1 && aws_iam_openid_connect_provider.github[0].url == "https://token.actions.githubusercontent.com"
    error_message = "명시적인 최초 설정에서만 공식 GitHub provider를 생성해야 합니다."
  }
}
run "reject_wildcard_subject" {
  command = plan
  variables { github_subject_prefix = "repo:example/*" }
  expect_failures = [var.github_subject_prefix]
}
run "new_rule_permission_does_not_bypass_parent_security_group_scope" {
  command = plan
  assert {
    condition = (
      jsondecode(aws_iam_role_policy.apply_rule_creation.policy).Statement[0].Resource == "arn:aws:ec2:ap-northeast-2:111122223333:security-group-rule/*" &&
      jsondecode(aws_iam_role_policy.apply_rule_creation.policy).Statement[0].Condition.StringEquals["aws:RequestTag/Project"] == "delta-platform" &&
      toset(jsondecode(aws_iam_role_policy.apply_rule_creation.policy).Statement[0].Action) == toset(["ec2:AuthorizeSecurityGroupIngress", "ec2:AuthorizeSecurityGroupEgress"])
    )
    error_message = "새 규칙 ARN과 요청 태그만 허용하며 다른 부모 SG를 허용하면 안 됩니다."
  }
}
