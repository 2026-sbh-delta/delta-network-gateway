terraform {
  required_version = "= 1.16.4"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "= 6.71.0"
    }
  }
  backend "s3" {}
}

provider "aws" {
  region              = "ap-northeast-2"
  allowed_account_ids = ["616723161368"]
}

locals {
  groups = ["/delta-cp/central/aws", "/delta-cp/central/gcp", "/delta-cp/platform/orchestrator"]
  evidence_policy = {
    Version = "2012-10-17"
    Statement = [{
      Sid      = "ReadDiagnosticEvidence"
      Effect   = "Allow"
      Action   = ["logs:FilterLogEvents"]
      Resource = [for group in local.groups : "arn:aws:logs:ap-northeast-2:616723161368:log-group:${group}:*"]
    }]
  }
}

# Separate additive policy: the team's base role and deployment permissions stay under their existing root.
resource "aws_iam_role_policy" "evidence" {
  name   = "delta-cp-diagnostic-log-read"
  role   = "delta-cp-orchestrator"
  policy = jsonencode(local.evidence_policy)
}

output "role_name" {
  value = aws_iam_role_policy.evidence.role
}
output "log_groups" {
  value = local.groups
}
