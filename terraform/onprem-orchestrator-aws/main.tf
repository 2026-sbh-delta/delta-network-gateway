terraform {
  required_version = "= 1.16.4"
  required_providers {
    aws = { source = "hashicorp/aws", version = "= 6.71.0" }
  }
  backend "s3" {}
}
provider "aws" {
  region = "ap-northeast-2"
  allowed_account_ids = ["616723161368"]
}
locals {
  content = file("${path.module}/../../deploy/onprem-runtime/document.json")
}
resource "aws_ssm_document" "bridge" {
  name = "delta-cp-onprem-bridge-${substr(sha256(local.content), 0, 12)}"
  document_type = "Command"
  document_format = "JSON"
  content = local.content
  lifecycle { create_before_destroy = true }
}
resource "aws_iam_role_policy" "bridge" {
  name = "delta-cp-onprem-bridge"
  role = "delta-cp-orchestrator"
  policy = jsonencode({Version="2012-10-17", Statement=[
    {Effect="Allow", Action=["ssm:SendCommand"], Resource=[aws_ssm_document.bridge.arn,"arn:aws:ec2:ap-northeast-2:616723161368:instance/i-0e5df7881a7de8fbd",aws_ssm_document.legacy_bridge.arn]},
    {Effect="Allow", Action=["ssm:GetCommandInvocation"], Resource="*"}
  ]})
}
output "document_name" { value = aws_ssm_document.bridge.name }
output "document_hash" { value = aws_ssm_document.bridge.hash }

resource "aws_ssm_document" "scanner" {
  name = "delta-cp-native-scanners-${substr(sha256(file("${path.module}/../../deploy/onprem-runtime/scanner-document.json")),0,12)}"
  document_type = "Command"
  document_format = "JSON"
  content = file("${path.module}/../../deploy/onprem-runtime/scanner-document.json")
  lifecycle { create_before_destroy = true }
}
resource "aws_iam_role_policy" "native_scanner" {
  name = "delta-native-scanners"
  role = "delta-platform-app"
  policy = jsonencode({Version="2012-10-17",Statement=[
    {Effect="Allow",Action=["ssm:SendCommand"],Resource=[aws_ssm_document.scanner.arn,
      "arn:aws:ec2:ap-northeast-2:616723161368:instance/i-01e002ddcf92c7b82",
      "arn:aws:ec2:ap-northeast-2:616723161368:instance/i-0f7ce2c84581c4e35"]},
    {Effect="Allow",Action=["ssm:GetCommandInvocation"],Resource="*"}
  ]})
}
output "scanner_document_name" { value=aws_ssm_document.scanner.name }
output "scanner_document_hash" { value=aws_ssm_document.scanner.hash }

# Preserve the already deployed legacy client for rollback; the current bridge uses its independent executor.
resource "aws_ssm_document" "legacy_bridge" {
  name="delta-cp-onprem-bridge-a209f55948f0"
  document_type="Command"
  document_format="JSON"
  content=file("${path.module}/../../deploy/onprem-runtime/legacy-document.json")
}
