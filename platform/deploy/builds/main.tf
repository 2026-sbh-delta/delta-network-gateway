locals {
  project_arn = "arn:aws:codebuild:${var.region}:${var.account_id}:project/${var.name}"
}
resource "aws_ecr_repository" "customer" {
  name                 = var.name
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration { encryption_type = "AES256" }
}
resource "aws_cloudwatch_log_group" "build" {
  name              = "/aws/codebuild/${var.name}"
  retention_in_days = 7
}
resource "aws_iam_role" "build" {
  name = var.name
  assume_role_policy = jsonencode({
    Version = "2012-10-17", Statement = [{
      Effect    = "Allow", Action = "sts:AssumeRole", Principal = { Service = "codebuild.amazonaws.com" },
      Condition = { StringEquals = { "aws:SourceAccount" = var.account_id }, ArnEquals = { "aws:SourceArn" = local.project_arn } }
    }]
  })
}
resource "aws_iam_role_policy" "build" {
  name = "isolated-image-build"
  role = aws_iam_role.build.id
  policy = jsonencode({
    Version = "2012-10-17", Statement = [
      { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
      { Effect = "Allow", Action = ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage"], Resource = aws_ecr_repository.customer.arn },
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.build.arn}:*" }
    ]
  })
}
resource "aws_codebuild_project" "customer" {
  name                   = var.name
  description            = "공개 저장소 고정 커밋의 고객 Dockerfile 빌드 전용"
  service_role           = aws_iam_role.build.arn
  build_timeout          = 15
  queued_timeout         = 10
  concurrent_build_limit = 1
  artifacts { type = "NO_ARTIFACTS" }
  cache { type = "NO_CACHE" }
  environment {
    compute_type                = "BUILD_GENERAL1_SMALL"
    image                       = "aws/codebuild/standard:7.0"
    type                        = "LINUX_CONTAINER"
    privileged_mode             = true
    image_pull_credentials_type = "CODEBUILD"
    environment_variable {
      name  = "DELTA_REPOSITORY"
      value = aws_ecr_repository.customer.repository_url
    }
  }
  source {
    type                = "GITHUB"
    location            = "https://github.com/docker/welcome-to-docker.git"
    git_clone_depth     = 1
    report_build_status = false
    git_submodules_config { fetch_submodules = false }
    # Inline buildspec wins over every customer buildspec.yml. No repo scripts run on the host.
    buildspec = yamlencode({
      version = "0.2"
      phases = {
        build = {
          commands = ["python3 -I -c \"import base64; exec(compile(base64.b64decode('${filebase64("${path.module}/build.py")}'), '/trusted-build.py', 'exec'))\""]
        }
      }
    })
  }
  logs_config {
    cloudwatch_logs { group_name = aws_cloudwatch_log_group.build.name }
  }
  depends_on = [aws_iam_role_policy.build]
}
resource "aws_iam_role_policy" "platform_builds" {
  name = "customer-build-control"
  role = var.platform_role_name
  policy = jsonencode({
    Version = "2012-10-17", Statement = [
      { Effect = "Allow", Action = ["codebuild:StartBuild"], Resource = local.project_arn },
      { Effect = "Allow", Action = ["codebuild:BatchGetBuilds", "codebuild:StopBuild"], Resource = local.project_arn },
      { Effect = "Allow", Action = ["ecr:DescribeImages"], Resource = aws_ecr_repository.customer.arn },
      { Effect = "Allow", Action = ["logs:GetLogEvents"], Resource = "${aws_cloudwatch_log_group.build.arn}:*" }
    ]
  })
}
