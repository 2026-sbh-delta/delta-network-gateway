output "project_name" {
  value       = aws_codebuild_project.customer.name
  description = "Worker가 시작할 프로젝트"
}
output "repository_url" {
  value       = aws_ecr_repository.customer.repository_url
  description = "고객 이미지 전용 저장소"
}
output "build_role_arn" {
  value       = aws_iam_role.build.arn
  description = "인프라 관리 권한 없는 빌드 역할"
}
output "runtime_env" {
  value = {
    PLATFORM_BUILD_PROJECT    = aws_codebuild_project.customer.name
    PLATFORM_BUILD_REPOSITORY = aws_ecr_repository.customer.repository_url
    PLATFORM_BUILD_REGION     = var.region
  }
  description = "기존 SSM app_env에 병합할 공개 설정값"
}
