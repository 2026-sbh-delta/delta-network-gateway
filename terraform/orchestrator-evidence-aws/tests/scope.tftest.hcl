mock_provider "aws" {}

run "read_only_fixed_scope" {
  command = plan
  assert {
    condition     = aws_iam_role_policy.evidence.role == "delta-cp-orchestrator"
    error_message = "Only the existing orchestrator may receive log reads."
  }
  assert {
    condition     = jsondecode(aws_iam_role_policy.evidence.policy).Statement[0].Action == ["logs:FilterLogEvents"]
    error_message = "No write, role, secret, deployment, or wildcard actions are allowed."
  }
  assert {
    condition     = length(local.evidence_policy.Statement[0].Resource) == 3 && alltrue([for arn in local.evidence_policy.Statement[0].Resource : startswith(arn, "arn:aws:logs:ap-northeast-2:616723161368:log-group:/delta-cp/")])
    error_message = "Only the three fixed local log groups are readable."
  }
}
