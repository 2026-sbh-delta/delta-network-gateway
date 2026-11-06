mock_provider "aws" {}
run "fixed_native_scanners" {
  command=apply
  assert {
    condition=aws_iam_role_policy.native_scanner.role=="delta-platform-app" && length(jsondecode(aws_iam_role_policy.native_scanner.policy).Statement[0].Resource)==3
    error_message="Native Worker may call only the fixed document and two scanner instances."
  }
  assert {
    condition=startswith(aws_ssm_document.scanner.name,"delta-cp-native-scanners-") && jsondecode(aws_iam_role_policy.native_scanner.policy).Statement[1].Action[0]=="ssm:GetCommandInvocation"
    error_message="Pin scanner documents and do not grant list or arbitrary execution permissions."
  }
}
