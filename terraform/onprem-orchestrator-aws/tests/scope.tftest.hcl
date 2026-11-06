mock_provider "aws" {}
run "fixed_worker_document" {
  command = plan
  assert {
    condition = aws_iam_role_policy.bridge.role == "delta-cp-orchestrator" && startswith(aws_ssm_document.bridge.name,"delta-cp-onprem-bridge-")
    error_message = "Only the fixed role and immutable bridge document are supported."
  }
  assert {
    condition = jsondecode(aws_iam_role_policy.bridge.policy).Statement[0].Resource[1] == "arn:aws:ec2:ap-northeast-2:616723161368:instance/i-0e5df7881a7de8fbd"
    error_message = "Do not allow arbitrary execution destinations."
  }
}
