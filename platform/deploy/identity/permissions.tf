locals {
  state_read = [
    { Sid = "StateLocation", Effect = "Allow", Action = ["s3:GetBucketLocation"], Resource = local.state },
    { Sid = "StateList", Effect = "Allow", Action = ["s3:ListBucket"], Resource = local.state },
    { Sid = "StateRead", Effect = "Allow", Action = ["s3:GetObject"], Resource = local.state_object },
  ]
  plan_policy = {
    Version = "2012-10-17"
    Statement = concat(local.state_read, [
      { Sid = "StateLock", Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = local.lock_object },
      { Sid = "EC2Metadata", Effect = "Allow", Action = ["ec2:Describe*"], Resource = "*", Condition = { StringEquals = { "aws:RequestedRegion" = var.region } } },
      { Sid = "VMRoleRead", Effect = "Allow", Action = ["iam:GetRole", "iam:GetRolePolicy", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies", "iam:ListInstanceProfilesForRole"], Resource = local.vm_roles },
      { Sid = "VMProfileRead", Effect = "Allow", Action = ["iam:GetInstanceProfile"], Resource = local.vm_profiles },
      { Sid = "ArtifactBucketConfiguration", Effect = "Allow", Action = [
        "s3:GetBucketLocation", "s3:GetBucketTagging", "s3:GetBucketVersioning", "s3:GetEncryptionConfiguration", "s3:GetBucketPublicAccessBlock",
        "s3:GetBucketOwnershipControls", "s3:GetBucketPolicy", "s3:GetBucketAcl", "s3:GetBucketCors", "s3:GetBucketLogging", "s3:GetBucketRequestPayment",
        "s3:GetBucketWebsite", "s3:GetReplicationConfiguration", "s3:GetAccelerateConfiguration", "s3:GetBucketObjectLockConfiguration", "s3:GetLifecycleConfiguration", "s3:ListBucket"
      ], Resource = local.bucket },
      { Sid = "RepositoryRead", Effect = "Allow", Action = ["ecr:DescribeRepositories", "ecr:ListTagsForResource", "ecr:GetRepositoryPolicy", "ecr:GetLifecyclePolicy"], Resource = local.ecr },
      { Sid = "CloudFrontRead", Effect = "Allow", Action = ["cloudfront:GetDistribution", "cloudfront:GetDistributionConfig", "cloudfront:GetVpcOrigin", "cloudfront:ListTagsForResource"], Resource = ["${local.cloudfront}:distribution/*", "${local.cloudfront}:vpcorigin/*"], Condition = local.cloud_project },
      { Sid = "OriginRequestPolicyRead", Effect = "Allow", Action = ["cloudfront:GetOriginRequestPolicy", "cloudfront:GetOriginRequestPolicyConfig"], Resource = "${local.cloudfront}:origin-request-policy/${var.origin_request_policy_id}" }
    ])
  }
  apply_policy = {
    Version = "2012-10-17"
    Statement = [
      { Sid = "StateWrite", Effect = "Allow", Action = ["s3:PutObject"], Resource = local.state_object },
      { Sid = "MaintainTaggedEC2", Effect = "Allow", Action = [
        "ec2:StartInstances", "ec2:StopInstances", "ec2:RebootInstances", "ec2:TerminateInstances", "ec2:ModifyInstanceAttribute", "ec2:ModifyInstanceCreditSpecification", "ec2:ModifyInstanceMetadataOptions",
        "ec2:ModifyVolume", "ec2:AttachVolume", "ec2:DetachVolume", "ec2:DeleteVolume", "ec2:ModifyVpcAttribute", "ec2:ModifySubnetAttribute",
        "ec2:CreateRoute", "ec2:ReplaceRoute", "ec2:DeleteRoute", "ec2:AssociateRouteTable", "ec2:DisassociateRouteTable", "ec2:ReplaceRouteTableAssociation",
        "ec2:AuthorizeSecurityGroupIngress", "ec2:AuthorizeSecurityGroupEgress", "ec2:RevokeSecurityGroupIngress", "ec2:RevokeSecurityGroupEgress", "ec2:ModifySecurityGroupRules",
        "ec2:DeleteSecurityGroup", "ec2:DeleteSubnet", "ec2:DeleteRouteTable", "ec2:DeleteInternetGateway", "ec2:DeleteVpc", "ec2:AttachInternetGateway", "ec2:DetachInternetGateway",
        "ec2:AssociateAddress", "ec2:DisassociateAddress", "ec2:ReleaseAddress", "ec2:AssociateIamInstanceProfile", "ec2:ReplaceIamInstanceProfileAssociation", "ec2:DisassociateIamInstanceProfile"
      ], Resource = "${local.ec2}:*/*", Condition = local.project },
      { Sid = "CreateTaggedEC2", Effect = "Allow", Action = ["ec2:CreateVolume", "ec2:CreateSecurityGroup", "ec2:CreateSubnet", "ec2:CreateRouteTable", "ec2:CreateInternetGateway", "ec2:CreateVpc", "ec2:AllocateAddress"], Resource = "${local.ec2}:*/*", Condition = { StringEquals = { "aws:RequestTag/Project" = var.name, "aws:RequestedRegion" = var.region } } },
      { Sid = "CreateWithinPlatformVpc", Effect = "Allow", Action = ["ec2:CreateSecurityGroup", "ec2:CreateSubnet", "ec2:CreateRouteTable"], Resource = "${local.ec2}:vpc/${var.platform_vpc_id}" },
      { Sid = "LaunchTaggedVM", Effect = "Allow", Action = ["ec2:RunInstances"], Resource = "${local.ec2}:instance/*", Condition = { StringEquals = { "aws:RequestTag/Project" = var.name, "ec2:InstanceType" = ["t4g.micro", "t4g.small", "t4g.medium"] } } },
      { Sid = "LaunchWithPlatformNetwork", Effect = "Allow", Action = ["ec2:RunInstances"], Resource = concat([for id in var.platform_subnet_ids : "${local.ec2}:subnet/${id}"], ["${local.ec2}:security-group/*"]), Condition = local.project },
      { Sid = "LaunchImagesAndVolumes", Effect = "Allow", Action = ["ec2:RunInstances"], Resource = ["arn:aws:ec2:${var.region}::image/*", "${local.ec2}:volume/*"] },
      { Sid = "LaunchNetworkInterfaces", Effect = "Allow", Action = ["ec2:RunInstances"], Resource = "${local.ec2}:network-interface/*", Condition = { ArnEquals = { "ec2:Subnet" = [for id in var.platform_subnet_ids : "${local.ec2}:subnet/${id}"] } } },
      { Sid = "TagAtCreation", Effect = "Allow", Action = ["ec2:CreateTags"], Resource = "${local.ec2}:*/*", Condition = { StringEquals = { "aws:RequestTag/Project" = var.name, "ec2:CreateAction" = ["RunInstances", "CreateVolume", "CreateSecurityGroup", "CreateSubnet", "CreateRouteTable", "CreateInternetGateway", "CreateVpc", "AllocateAddress", "AuthorizeSecurityGroupIngress", "AuthorizeSecurityGroupEgress"] } } },
      { Sid = "RetagOwnedEC2", Effect = "Allow", Action = ["ec2:CreateTags"], Resource = "${local.ec2}:*/*", Condition = merge(local.project, { StringEqualsIfExists = { "aws:RequestTag/Project" = var.name } }) },
      { Sid = "RemoveNonOwnershipTags", Effect = "Allow", Action = ["ec2:DeleteTags"], Resource = "${local.ec2}:*/*", Condition = merge(local.project, { "ForAllValues:StringNotEquals" = { "aws:TagKeys" = ["Project"] } }) },
      { Sid = "PassOnlyVMRoles", Effect = "Allow", Action = ["iam:PassRole"], Resource = local.vm_roles, Condition = { StringEquals = { "iam:PassedToService" = "ec2.amazonaws.com" } } },
      { Sid = "MaintainDistributionAndOrigin", Effect = "Allow", Action = ["cloudfront:UpdateDistribution", "cloudfront:DeleteDistribution", "cloudfront:UpdateVpcOrigin", "cloudfront:DeleteVpcOrigin", "cloudfront:TagResource", "cloudfront:UntagResource"], Resource = ["${local.cloudfront}:distribution/*", "${local.cloudfront}:vpcorigin/*"], Condition = local.cloud_project },
      { Sid = "MaintainOriginRequestPolicy", Effect = "Allow", Action = ["cloudfront:UpdateOriginRequestPolicy", "cloudfront:DeleteOriginRequestPolicy"], Resource = "${local.cloudfront}:origin-request-policy/${var.origin_request_policy_id}" },
      { Sid = "MaintainArtifactBucket", Effect = "Allow", Action = ["s3:PutBucketTagging", "s3:PutBucketVersioning", "s3:PutEncryptionConfiguration", "s3:PutBucketPublicAccessBlock", "s3:PutBucketOwnershipControls", "s3:PutBucketPolicy", "s3:PutLifecycleConfiguration"], Resource = local.bucket },
      { Sid = "MaintainRepository", Effect = "Allow", Action = ["ecr:PutImageTagMutability", "ecr:PutImageScanningConfiguration", "ecr:TagResource", "ecr:UntagResource"], Resource = local.ecr }
    ]
  }
  release_policy = {
    Version = "2012-10-17"
    Statement = concat(local.state_read, [
      { Sid = "ECRAuthentication", Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
      { Sid = "PlatformImages", Effect = "Allow", Action = ["ecr:DescribeRepositories", "ecr:DescribeImages", "ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage"], Resource = local.ecr },
      { Sid = "ReleaseArchive", Effect = "Allow", Action = ["s3:PutObject"], Resource = "${local.bucket}/releases/*" },
      { Sid = "RunDeploymentDocument", Effect = "Allow", Action = ["ssm:SendCommand"], Resource = "arn:aws:ssm:${var.region}::document/AWS-RunShellScript" },
      { Sid = "RunOnlyOnPlatformApp", Effect = "Allow", Action = ["ssm:SendCommand"], Resource = "${local.ec2}:instance/*", Condition = { StringEquals = { "ssm:resourceTag/Project" = var.name, "ssm:resourceTag/Name" = "${var.name}-app" } } },
      { Sid = "DeploymentResultAndParameterMetadata", Effect = "Allow", Action = ["ssm:GetCommandInvocation", "ssm:DescribeParameters"], Resource = "*", Condition = { StringEquals = { "aws:RequestedRegion" = var.region } } }
    ])
  }
}
