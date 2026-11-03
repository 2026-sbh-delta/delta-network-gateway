mock_provider "aws" {
  mock_data "aws_ecr_repository" {
    defaults = { arn = "arn:aws:ecr:ap-northeast-2:111122223333:repository/delta-platform-builds", repository_url = "111122223333.dkr.ecr.ap-northeast-2.amazonaws.com/delta-platform-builds" }
  }
}
variables {
  account_id                 = "111122223333"
  verification_principal_arn = null
}
run "agent_can_only_publish_images" {
  command = plan
  assert {
    condition     = length(jsondecode(aws_iam_role.agent.assume_role_policy).Statement) == 1
    error_message = "검증 주체를 기본으로 허용하지 않습니다."
  }
  assert {
    condition     = alltrue([for s in jsondecode(aws_iam_role_policy.publish.policy).Statement : alltrue([for action in s.Action : startswith(action, "ecr:")])]) && jsondecode(aws_iam_role_policy.publish.policy).Statement[1].Resource == "arn:aws:ecr:ap-northeast-2:111122223333:repository/delta-platform-builds"
    error_message = "에이전트는 지정 ECR 이미지 권한만 가져야 합니다."
  }
}
run "reject_external_verification_role" {
  command = plan
  variables { verification_principal_arn = "arn:aws:iam::999999999999:role/admin" }
  expect_failures = [var.verification_principal_arn]
}
run "runtime_secrets_are_component_scoped" {
  command = plan
  assert {
    condition     = alltrue([for component, policy in aws_iam_role_policy.runtime : jsondecode(policy.policy).Statement[2].Resource == "arn:aws:ssm:ap-northeast-2:111122223333:parameter/delta-platform/jenkins/${component}" && alltrue([for s in jsondecode(policy.policy).Statement : alltrue([for action in s.Action : !contains(["ssm:PutParameter", "ec2:RunInstances", "ecs:UpdateService", "iam:PassRole"], action)])])])
    error_message = "각 VM은 자기 구성만 읽고 다른 플랫폼 자원을 조작하지 않아야 합니다."
  }
}
