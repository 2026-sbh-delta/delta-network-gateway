mock_provider "aws" {
  mock_data "aws_subnet" { defaults = { vpc_id = "vpc-00000000000000001", cidr_block = "10.80.1.0/24", availability_zone = "ap-northeast-2a" } }
  mock_data "aws_vpc" { defaults = { cidr_block = "10.80.0.0/16" } }
  mock_data "aws_instance" { defaults = { network_interface_id = "eni-00000000000000001", source_dest_check = false, vpc_security_group_ids = ["sg-00000000000000001"] } }
  mock_data "aws_network_interface" { defaults = { vpc_id = "vpc-00000000000000001" } }
  mock_data "aws_ecr_repository" { defaults = { arn = "arn:aws:ecr:ap-northeast-2:111122223333:repository/delta-platform-builds", repository_url = "111122223333.dkr.ecr.ap-northeast-2.amazonaws.com/delta-platform-builds" } }
  mock_resource "aws_iam_role" { defaults = { arn = "arn:aws:iam::111122223333:role/delta-platform-sandbox" } }
  mock_resource "aws_lb" { defaults = { arn = "arn:aws:elasticloadbalancing:ap-northeast-2:111122223333:loadbalancer/app/sandbox/1234567890123456" } }
  mock_resource "aws_lb_target_group" { defaults = { arn = "arn:aws:elasticloadbalancing:ap-northeast-2:111122223333:targetgroup/sandbox/1234567890123456" } }
  mock_resource "aws_cloudwatch_log_group" { defaults = { arn = "arn:aws:logs:ap-northeast-2:111122223333:log-group:/ecs/delta-platform-sandbox" } }
  mock_resource "aws_ecs_cluster" { defaults = { arn = "arn:aws:ecs:ap-northeast-2:111122223333:cluster/delta-platform-sandbox" } }
  mock_resource "aws_ecs_task_definition" { defaults = { arn = "arn:aws:ecs:ap-northeast-2:111122223333:task-definition/delta-platform-sandbox:1" } }
}
variables {
  account_id                 = "111122223333"
  platform_private_subnet_id = "subnet-00000000000000001"
  nat_instance_id            = "i-00000000000000001"
  bootstrap_image_digest     = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}
run "private_bounded_runtime" {
  command = apply
  assert {
    condition     = aws_ecs_service.sandbox.desired_count == 0 && aws_ecs_task_definition.sandbox.cpu == "256" && aws_ecs_task_definition.sandbox.memory == "512" && aws_ecs_service.sandbox.deployment_maximum_percent == 100
    error_message = "대기 중 태스크는 0개, 실행 중 최소 크기 1개여야 합니다."
  }
  assert {
    condition     = aws_lb.sandbox.internal && !aws_ecs_service.sandbox.network_configuration[0].assign_public_ip && !aws_subnet.task.map_public_ip_on_launch
    error_message = "고객 샌드박스를 공인 주소로 노출하지 않습니다."
  }
  assert {
    condition     = aws_subnet.alb.cidr_block == "10.80.2.0/24" && aws_subnet.task.cidr_block == "10.80.3.0/24" && aws_vpc_security_group_ingress_rule.nat_https.cidr_ipv4 == "10.80.3.0/24"
    error_message = "지정된 플랫폼 대역 내에서만 네트워크를 확장합니다."
  }
  assert {
    condition     = aws_vpc_security_group_ingress_rule.task.referenced_security_group_id == aws_security_group.alb.id && aws_vpc_security_group_egress_rule.task_https.from_port == 443 && aws_vpc_security_group_egress_rule.task_https.to_port == 443
    error_message = "태스크의 앱 포트는 ALB에만 열고 egress는 HTTPS로 제한합니다."
  }
  assert {
    condition     = !aws_ecs_service.sandbox.enable_execute_command && aws_ecs_task_definition.sandbox.runtime_platform[0].cpu_architecture == "X86_64"
    error_message = "고정 amd64 빌드와 같은 실행 구조이며 ECS Exec는 사용하지 않습니다."
  }
  assert {
    condition     = alltrue([for s in jsondecode(aws_iam_role_policy.execution.policy).Statement : alltrue([for a in s.Action : startswith(a, "ecr:") || startswith(a, "logs:")])])
    error_message = "이미지 실행 역할은 ECR 읽기와 로그 쓰기로 제한합니다."
  }
  assert {
    condition     = alltrue([for s in jsondecode(aws_iam_role_policy.platform_sandbox.policy).Statement : s.Resource == "arn:aws:ecs:ap-northeast-2:111122223333:service/delta-platform-sandbox/delta-platform-sandbox" if contains(s.Action, "ecs:UpdateService")])
    error_message = "기존 팀 ECS 서비스를 변경할 권한을 주면 안 됩니다."
  }
  assert {
    condition     = alltrue([for a in ["ecs:DescribeTaskDefinition", "elasticloadbalancing:DescribeTargetHealth"] : anytrue([for s in jsondecode(aws_iam_role_policy.platform_sandbox.policy).Statement : contains(s.Action, a) && s.Resource == "*"])])
    error_message = "태스크 정의·헬스 조회 API는 AWS에서 리소스 단위 권한을 지원하지 않습니다."
  }
  assert {
    condition     = !jsondecode(output.runtime_env.PLATFORM_SANDBOX_TARGET).dynamic_scanner_access && jsondecode(output.runtime_env.PLATFORM_SANDBOX_TARGET).allowed_image_prefixes == ["111122223333.dkr.ecr.ap-northeast-2.amazonaws.com/delta-platform-builds@"]
    error_message = "지정한 빌드 저장소의 digest 이미지만 받으며 공개 scanner 규칙은 만들지 않습니다."
  }
}
run "reject_same_az" {
  command = plan
  variables { second_az = "ap-northeast-2a" }
  expect_failures = [aws_subnet.alb]
}
