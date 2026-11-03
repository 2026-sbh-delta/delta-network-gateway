mock_provider "aws" {
  mock_resource "aws_iam_role" { defaults = { arn = "arn:aws:iam::111122223333:role/delta-platform-builds" } }
  mock_resource "aws_ecr_repository" { defaults = { arn = "arn:aws:ecr:ap-northeast-2:111122223333:repository/delta-platform-builds", repository_url = "111122223333.dkr.ecr.ap-northeast-2.amazonaws.com/delta-platform-builds" } }
  mock_resource "aws_cloudwatch_log_group" { defaults = { arn = "arn:aws:logs:ap-northeast-2:111122223333:log-group:/aws/codebuild/delta-platform-builds" } }
}
variables { account_id = "111122223333" }
run "isolated_build" {
  command = apply
  assert {
    condition     = aws_codebuild_project.customer.concurrent_build_limit == 1 && aws_codebuild_project.customer.build_timeout == 15 && aws_codebuild_project.customer.queued_timeout == 10
    error_message = "빌드 동시 실행·시간 제한이 필요합니다."
  }
  assert {
    condition     = aws_ecr_repository.customer.image_tag_mutability == "IMMUTABLE" && !aws_ecr_repository.customer.force_delete
    error_message = "이미지 태그 덮어쓰기·강제 삭제를 허용하지 않습니다."
  }
  assert {
    condition     = length(aws_codebuild_project.customer.vpc_config) == 0 && aws_codebuild_project.customer.environment[0].compute_type == "BUILD_GENERAL1_SMALL"
    error_message = "고객 빌드를 운영망 밖의 최소 on-demand 환경에서 실행합니다."
  }
  assert {
    condition     = alltrue([for s in jsondecode(aws_iam_role_policy.build.policy).Statement : alltrue([for a in s.Action : startswith(a, "ecr:") || startswith(a, "logs:")])])
    error_message = "빌드 역할에는 이미지·로그 외의 관리 권한이 없어야 합니다."
  }
  assert {
    condition     = !strcontains(jsonencode(jsondecode(aws_iam_role_policy.platform_builds.policy).Statement), "iam:PassRole")
    error_message = "플랫폼 Worker가 다른 빌드 역할을 지정할 수 없어야 합니다."
  }
}
