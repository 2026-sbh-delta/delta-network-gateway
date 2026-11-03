mock_provider "aws" {
  override_during = plan
  mock_data "aws_caller_identity" { defaults = { account_id = "111122223333" } }
}

variables {
  account_id = "111122223333"
}

run "telemetry_is_read_only_and_logs_are_scoped" {
  command = plan
  assert {
    condition = alltrue(flatten([
      for statement in jsondecode(aws_iam_role_policy.observability_read.policy).Statement : [
        for action in statement.Action : !can(regex(":(Put|Create|Delete|Update|Start|Stop)", action))
      ]
    ]))
    error_message = "대시보드 역할에는 수집·변경 권한을 주지 않습니다."
  }
  assert {
    condition     = jsondecode(aws_iam_role_policy.observability_read.policy).Statement[1].Resource == "arn:aws:logs:ap-northeast-2:111122223333:log-group:/delta-platform/apps/*:log-stream:application"
    error_message = "고객 관측 전용 로그 경로만 조회해야 합니다."
  }
}
