terraform {
  required_version = ">= 1.11, < 2.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}
provider "aws" {
  region              = "ap-northeast-2"
  allowed_account_ids = [var.account_id]
  default_tags {
    tags = { Project = "delta-platform", ManagedBy = "terraform", Component = "deployment-runtime" }
  }
}
locals {
  prefix = "arn:aws"
  ec2    = "arn:aws:ec2:ap-northeast-2:${var.account_id}"
  rds    = "arn:aws:rds:ap-northeast-2:${var.account_id}"
  secret = "arn:aws:secretsmanager:ap-northeast-2:${var.account_id}:secret:"
}
resource "aws_subnet" "customer" {
  vpc_id                  = var.vpc_id
  cidr_block              = "10.80.60.0/24"
  availability_zone       = "ap-northeast-2a"
  map_public_ip_on_launch = false
  tags                    = { Name = "delta-platform-customer-apps" }
}
resource "aws_subnet" "database" {
  for_each                = { "a" = "10.80.61.0/24", "b" = "10.80.62.0/24" }
  vpc_id                  = var.vpc_id
  cidr_block              = each.value
  availability_zone       = "ap-northeast-2${each.key}"
  map_public_ip_on_launch = false
  tags                    = { Name = "delta-platform-customer-db-${each.key}" }
}
resource "aws_route_table" "customer" {
  vpc_id = var.vpc_id
  route {
    cidr_block           = "0.0.0.0/0"
    network_interface_id = var.nat_eni
  }
  tags = { Name = "delta-platform-customer-egress" }
}
resource "aws_route_table_association" "customer" {
  subnet_id      = aws_subnet.customer.id
  route_table_id = aws_route_table.customer.id
}
resource "aws_vpc_security_group_ingress_rule" "nat_customer" {
  security_group_id = var.nat_security_group
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  cidr_ipv4         = aws_subnet.customer.cidr_block
  description       = "HTTPS egress from platform-owned customer app subnet"
}
resource "aws_security_group" "role" {
  for_each    = toset(["deployer", "customer", "database"])
  name_prefix = "delta-platform-${each.key}-"
  vpc_id      = var.vpc_id
  description = "Platform ${each.key} boundary"
}
resource "aws_vpc_security_group_egress_rule" "https" {
  for_each          = toset(["deployer", "customer"])
  security_group_id = aws_security_group.role[each.key].id
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  cidr_ipv4         = "0.0.0.0/0"
}
resource "aws_vpc_security_group_ingress_rule" "database" {
  for_each                     = toset(["deployer", "customer"])
  security_group_id            = aws_security_group.role["database"].id
  referenced_security_group_id = aws_security_group.role[each.key].id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}
resource "aws_vpc_security_group_egress_rule" "database" {
  for_each                     = toset(["deployer", "customer"])
  security_group_id            = aws_security_group.role[each.key].id
  referenced_security_group_id = aws_security_group.role["database"].id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}
resource "aws_vpc_security_group_ingress_rule" "app" {
  security_group_id            = aws_security_group.role["customer"].id
  referenced_security_group_id = aws_security_group.role["deployer"].id
  from_port                    = 8088
  to_port                      = 8088
  ip_protocol                  = "tcp"
}
resource "aws_vpc_security_group_egress_rule" "app" {
  security_group_id            = aws_security_group.role["deployer"].id
  referenced_security_group_id = aws_security_group.role["customer"].id
  from_port                    = 8088
  to_port                      = 8088
  ip_protocol                  = "tcp"
}
resource "aws_vpc_security_group_ingress_rule" "controller" {
  security_group_id            = var.controller_security_group
  referenced_security_group_id = aws_security_group.role["deployer"].id
  from_port                    = 443
  to_port                      = 443
  ip_protocol                  = "tcp"
}
resource "aws_iam_role" "role" {
  for_each = toset(["deployer", "customer"])
  name     = "delta-platform-${each.key}"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "ec2.amazonaws.com" } }]
  })
}
resource "aws_iam_instance_profile" "role" {
  for_each = aws_iam_role.role
  name     = each.value.name
  role     = each.value.name
}
resource "aws_iam_role_policy_attachment" "ssm" {
  for_each   = aws_iam_role.role
  role       = each.value.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
resource "aws_iam_role_policy" "images" {
  for_each = aws_iam_role.role
  name     = "deployment-images-and-database"
  role     = each.value.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
      { Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"], Resource = "arn:aws:ecr:ap-northeast-2:${var.account_id}:repository/delta-platform-builds" },
      { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = "${local.secret}delta-platform/customer-db/*" }
    ]
  })
}
resource "aws_iam_role_policy" "deployment" {
  name = "trusted-deployment"
  role = aws_iam_role.role["deployer"].name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["ssm:GetParameter"], Resource = "arn:aws:ssm:ap-northeast-2:${var.account_id}:parameter/delta-platform/deployment/agent" },
      { Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"], Resource = "arn:aws:ecr:ap-northeast-2:${var.account_id}:repository/delta-platform" },
      { Effect = "Allow", Action = ["ec2:Describe*", "rds:Describe*", "ssm:DescribeInstanceInformation", "ssm:GetCommandInvocation"], Resource = "*" },
      { Effect = "Allow", Action = ["ec2:RunInstances"], Resource = ["arn:aws:ec2:ap-northeast-2::image/${var.ami_id}", "${local.ec2}:subnet/${aws_subnet.customer.id}", "${local.ec2}:security-group/${aws_security_group.role["customer"].id}"] },
      { Effect = "Allow", Action = ["ec2:RunInstances"], Resource = "${local.ec2}:instance/*", Condition = { StringEquals = { "ec2:InstanceProfile" = aws_iam_instance_profile.role["customer"].arn, "aws:RequestTag/ManagedBy" = "delta-platform" } } },
      { Effect = "Allow", Action = ["ec2:RunInstances"], Resource = ["${local.ec2}:volume/*", "${local.ec2}:network-interface/*"] },
      { Effect = "Allow", Action = ["ec2:CreateTags"], Resource = ["${local.ec2}:instance/*", "${local.ec2}:volume/*", "${local.ec2}:network-interface/*"], Condition = { StringEquals = { "ec2:CreateAction" = "RunInstances" } } },
      { Effect = "Allow", Action = ["ec2:TerminateInstances", "ec2:ModifyInstanceAttribute"], Resource = "${local.ec2}:instance/*", Condition = { StringEquals = { "ec2:ResourceTag/ManagedBy" = "delta-platform" } } },
      { Effect = "Allow", Action = ["iam:PassRole"], Resource = aws_iam_role.role["customer"].arn, Condition = { StringEquals = { "iam:PassedToService" = "ec2.amazonaws.com" } } },
      { Effect = "Allow", Action = ["ssm:SendCommand"], Resource = "arn:aws:ssm:ap-northeast-2::document/AWS-RunShellScript" },
      { Effect = "Allow", Action = ["ssm:SendCommand"], Resource = "${local.ec2}:instance/*", Condition = { StringEquals = { "ssm:resourceTag/ManagedBy" = "delta-platform" } } },
      { Effect = "Allow", Action = ["rds:CreateDBInstance", "rds:CreateDBSubnetGroup", "rds:CreateDBParameterGroup", "rds:ModifyDBParameterGroup", "rds:AddTagsToResource", "rds:ListTagsForResource"], Resource = ["${local.rds}:db:delta-platform-db-*", "${local.rds}:subgrp:delta-platform-db-*", "${local.rds}:pg:delta-platform-db-*"] },
      { Effect = "Allow", Action = ["secretsmanager:CreateSecret", "secretsmanager:TagResource"], Resource = "${local.secret}rds!db-*", Condition = { StringEquals = { "aws:CalledViaLast" = "rds.amazonaws.com" } } },
      { Effect = "Allow", Action = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"], Resource = "${local.secret}rds!db-*", Condition = { StringLike = { "secretsmanager:ResourceTag/aws:rds:primaryDBInstanceArn" = "${local.rds}:db:delta-platform-db-*" } } },
      { Effect = "Allow", Action = ["kms:DescribeKey"], Resource = "arn:aws:kms:ap-northeast-2:${var.account_id}:key/*", Condition = { StringEquals = { "kms:ViaService" = "secretsmanager.ap-northeast-2.amazonaws.com" } } },
      { Effect = "Allow", Action = ["secretsmanager:CreateSecret", "secretsmanager:TagResource", "secretsmanager:DescribeSecret", "secretsmanager:PutSecretValue"], Resource = "${local.secret}delta-platform/customer-db/*" }
    ]
  })
}
resource "aws_instance" "deployer" {
  ami                         = var.ami_id
  instance_type               = "t3.small"
  subnet_id                   = var.deployer_subnet_id
  vpc_security_group_ids      = [aws_security_group.role["deployer"].id]
  iam_instance_profile        = aws_iam_instance_profile.role["deployer"].name
  associate_public_ip_address = false
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }
  root_block_device {
    volume_size           = 20
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = false
  }
  user_data = <<-SCRIPT
    #!/bin/bash
    set -eu
    dnf install -y python3.12 java-21-amazon-corretto-headless git unzip
    useradd --create-home deployer
    install -d -m 0700 -o deployer -g deployer /var/lib/delta-deploy/state
    install -d -m 0755 /opt/delta /etc/delta
    touch /opt/delta/bootstrap-ready
  SCRIPT
  tags      = { Name = "delta-platform-deployer" }
}
