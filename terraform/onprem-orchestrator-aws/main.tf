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
    {Effect="Allow", Action=["ssm:SendCommand"], Resource=[aws_ssm_document.bridge.arn,"arn:aws:ec2:ap-northeast-2:616723161368:instance/i-0e5df7881a7de8fbd"]},
    {Effect="Allow", Action=["ssm:GetCommandInvocation"], Resource="*"}
  ]})
}
output "document_name" { value = aws_ssm_document.bridge.name }
output "document_hash" { value = aws_ssm_document.bridge.hash }
