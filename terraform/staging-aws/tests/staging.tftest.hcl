mock_provider "aws" {
  override_during = plan
  mock_data "aws_vpc" {
    defaults = { id = "vpc-00000000000000001", cidr_block = "10.10.0.0/16" }
  }
  mock_data "aws_prefix_list" {
    defaults = { id = "pl-00000000000000001", name = "com.amazonaws.ap-northeast-2.s3" }
  }
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
}

override_data {
  target = data.aws_subnet.selected["subnet-00000000000000001"]
  values = { id = "subnet-00000000000000001", vpc_id = "vpc-00000000000000001", availability_zone_id = "apne2-az1", map_public_ip_on_launch = false }
}
override_data {
  target = data.aws_subnet.selected["subnet-00000000000000002"]
  values = { id = "subnet-00000000000000002", vpc_id = "vpc-00000000000000001", availability_zone_id = "apne2-az2", map_public_ip_on_launch = true }
}

# 접속 비밀값 ARN이 plan에서 정해져야 컨테이너 정의(비밀값 참조)를 검사할 수 있다.
override_resource {
  override_during = plan
  target          = aws_secretsmanager_secret.runtime[0]
  values          = { arn = "arn:aws:secretsmanager:ap-northeast-2:444455556666:secret:delta-lab-staging/runtime-abc123" }
}
override_resource {
  override_during = plan
  target          = aws_security_group.alb
  values          = { id = "sg-00000000000000001" }
}
override_resource {
  override_during = plan
  target          = aws_security_group.service
  values          = { id = "sg-00000000000000002" }
}

variables {
  account_id      = "444455556666"
  vpc_id          = "vpc-00000000000000001"
  subnet_ids      = ["subnet-00000000000000001", "subnet-00000000000000002"]
  route_table_ids = ["rtb-00000000000000001", "rtb-00000000000000002"]
  scanner_cidrs   = ["10.10.1.0/24"]
  # 기존 단일 컨테이너 검사는 이 값으로 유지한다. FE+BE 검사는 각 run에서 true로 바꾼다.
  dual_container = false
}

run "private_staging_foundation" {
  command = plan
  assert {
    condition     = aws_lb.staging.internal && aws_lb.staging.drop_invalid_header_fields && aws_lb.staging.load_balancer_type == "application"
    error_message = "The staging ALB must be internal and drop invalid headers."
  }
  assert {
    condition     = !one(aws_ecs_service.app.network_configuration).assign_public_ip && aws_ecs_service.app.desired_count == 0 && aws_ecs_service.app.launch_type == "FARGATE"
    error_message = "Tasks start at zero on Fargate without public IPs."
  }
  assert {
    condition     = length(aws_vpc_security_group_ingress_rule.alb_from_scanner_cidr) == 1 && aws_vpc_security_group_ingress_rule.alb_from_scanner_cidr["10.10.1.0/24"].from_port == 80 && length(aws_vpc_security_group_ingress_rule.alb_from_scanner_sg) == 0
    error_message = "Only the registered scanner CIDR may reach the ALB listener."
  }
  assert {
    condition     = aws_vpc_security_group_ingress_rule.service_from_alb.referenced_security_group_id == aws_security_group.alb.id && aws_vpc_security_group_ingress_rule.service_from_alb.from_port == 8080
    error_message = "Tasks accept the container port from the ALB security group only."
  }
  assert {
    condition     = length(aws_vpc_endpoint.interface) == 3 && length(aws_vpc_endpoint.s3) == 1 && alltrue([for endpoint in aws_vpc_endpoint.interface : endpoint.private_dns_enabled])
    error_message = "Private subnets need ECR API/DKR, Logs and S3 endpoints to pull images."
  }
  assert {
    condition     = tolist(output.tool_config.allowed_image_prefixes) == tolist(["444455556666.dkr.ecr.ap-northeast-2.amazonaws.com/"]) && output.tool_config.cloud == "aws" && output.tool_config.container_name == "app"
    error_message = "The tool config defaults to this account's ECR only."
  }
  assert {
    condition     = one(aws_ecr_repository.app).image_tag_mutability == "IMMUTABLE" && one(one(aws_ecr_repository.app).image_scanning_configuration).scan_on_push && length(aws_ecr_lifecycle_policy.app) == 1
    error_message = "The staging repository keeps immutable, scanned images with a retention policy."
  }
}

run "scanner_security_group_without_endpoints" {
  command = plan
  variables {
    scanner_cidrs              = []
    scanner_security_group_ids = ["sg-00000000000000009"]
    create_vpc_endpoints       = false
    route_table_ids            = []
    create_ecr_repository      = false
  }
  assert {
    condition     = length(aws_vpc_security_group_ingress_rule.alb_from_scanner_sg) == 1 && length(aws_vpc_security_group_ingress_rule.alb_from_scanner_cidr) == 0
    error_message = "A scanner security group alone may reach the ALB."
  }
  assert {
    condition     = length(aws_vpc_endpoint.interface) == 0 && length(aws_vpc_endpoint.s3) == 0 && length(aws_security_group.endpoints) == 0 && length(aws_vpc_security_group_egress_rule.service_to_s3) == 0
    error_message = "Endpoints are optional for subnets that already have egress."
  }
  assert {
    condition     = length(aws_ecr_repository.app) == 0 && output.ecr_repository_url == null
    error_message = "The repository is optional when images come from an existing registry."
  }
}

run "rejects_public_scanner_cidr" {
  command = plan
  variables {
    scanner_cidrs = ["0.0.0.0/0"]
  }
  expect_failures = [var.scanner_cidrs]
}

run "rejects_non_canonical_scanner_cidr" {
  command = plan
  variables {
    scanner_cidrs = ["10.10.1.5/24"]
  }
  expect_failures = [var.scanner_cidrs]
}

run "requires_a_scanner_source" {
  command = plan
  variables {
    scanner_cidrs = []
  }
  expect_failures = [terraform_data.contract]
}

run "endpoints_need_route_tables" {
  command = plan
  variables {
    route_table_ids = []
  }
  expect_failures = [terraform_data.contract]
}

run "internet_facing_with_dynamic_scanner_access" {
  command = plan
  variables {
    alb_internet_facing  = true
    scanner_cidrs        = []
    scanner_public_cidrs = ["203.0.113.10/32"]
  }
  assert {
    condition     = !aws_lb.staging.internal && !one(aws_ecs_service.app.network_configuration).assign_public_ip
    error_message = "Only the ALB faces the internet; tasks still get no public IP."
  }
  assert {
    condition     = length(aws_vpc_security_group_ingress_rule.alb_from_scanner_public) == 1 && length(aws_vpc_security_group_ingress_rule.alb_from_scanner_cidr) == 0
    error_message = "Only the fixed scanner egress CIDR is opened statically."
  }
  assert {
    condition     = output.tool_config.dynamic_scanner_access && output.tool_config.alb_security_group_id == "sg-00000000000000001"
    error_message = "The tool must know it manages the runner's current IP on the ALB security group."
  }
  assert {
    condition     = one(aws_lb_target_group.app.health_check).interval == 5 && one(aws_lb_target_group.app.health_check).healthy_threshold == 2
    error_message = "Fast health checks keep the image swap short."
  }
}

run "internet_facing_needs_no_static_source" {
  command = plan
  variables {
    alb_internet_facing = true
    scanner_cidrs       = []
  }
  assert {
    condition     = !aws_lb.staging.internal && length(aws_vpc_security_group_ingress_rule.alb_from_scanner_public) == 0
    error_message = "With dynamic access the ALB starts closed until the tool adds the runner IP."
  }
}

run "rejects_open_public_scanner_cidr" {
  command = plan
  variables {
    alb_internet_facing  = true
    scanner_public_cidrs = ["0.0.0.0/0"]
  }
  expect_failures = [var.scanner_public_cidrs]
}

run "rejects_private_public_scanner_cidr" {
  command = plan
  variables {
    alb_internet_facing  = true
    scanner_public_cidrs = ["10.0.0.0/24"]
  }
  expect_failures = [var.scanner_public_cidrs]
}

run "public_cidrs_need_internet_facing" {
  command = plan
  variables {
    scanner_public_cidrs = ["203.0.113.10/32"]
  }
  expect_failures = [terraform_data.contract]
}

# tracecompose-delivery-20261106 / IMPLEMENTATION_ONLY / pre_gateREVISED.
# Only mock-provider plans. Fixtures contain immutable image refs, never credentials.
run "single_container_default_remains_compatible" {
  command = plan
  assert {
    condition = (
      length(jsondecode(aws_ecs_task_definition.app.container_definitions)) == 1 &&
      jsondecode(aws_ecs_task_definition.app.container_definitions)[0].name == "app" &&
      jsondecode(aws_ecs_task_definition.app.container_definitions)[0].image == var.bootstrap_image &&
      one(jsondecode(aws_ecs_task_definition.app.container_definitions)[0].portMappings).containerPort == 8080 &&
      one(aws_ecs_service.app.load_balancer).container_name == "app"
    )
    error_message = "Omitting dual_container must preserve the existing app/8080 bootstrap and service binding."
  }
}

# Proposed inputs: dual_container=false, bootstrap_backend_image; backend name defaults to backend.
# Before those inputs exist Terraform warns, but failures below inspect real existing planned resources.
run "dual_bootstrap_is_one_task_with_loopback_backend" {
  command = plan
  variables {
    dual_container          = true
    container_name          = "frontend"
    container_port          = 3000
    bootstrap_image         = "444455556666.dkr.ecr.ap-northeast-2.amazonaws.com/frontend@sha256:1111111111111111111111111111111111111111111111111111111111111111"
    bootstrap_backend_image = "444455556666.dkr.ecr.ap-northeast-2.amazonaws.com/backend@sha256:2222222222222222222222222222222222222222222222222222222222222222"
    alb_internet_facing     = true
    scanner_cidrs           = []
    scanner_public_cidrs    = ["54.116.158.85/32"]
  }
  assert {
    condition = (
      aws_ecs_task_definition.app.network_mode == "awsvpc" &&
      length(jsondecode(aws_ecs_task_definition.app.container_definitions)) == 2 &&
      toset([for c in jsondecode(aws_ecs_task_definition.app.container_definitions) : c.name]) == toset(["frontend", "backend"]) &&
      alltrue([for c in jsondecode(aws_ecs_task_definition.app.container_definitions) : c.essential])
    )
    error_message = "Dual bootstrap needs two essential FE/BE containers together in the existing awsvpc task, not a second service."
  }
  assert {
    condition = try(
      one([for c in jsondecode(aws_ecs_task_definition.app.container_definitions) : c.image if c.name == "frontend"]) == var.bootstrap_image &&
      one([for c in jsondecode(aws_ecs_task_definition.app.container_definitions) : c.image if c.name == "backend"]) == "444455556666.dkr.ecr.ap-northeast-2.amazonaws.com/backend@sha256:2222222222222222222222222222222222222222222222222222222222222222",
      false
    )
    error_message = "The task must retain both independent immutable bootstrap image references."
  }
  assert {
    condition = try(
      one([for c in jsondecode(aws_ecs_task_definition.app.container_definitions) : { for e in c.environment : e.name => e.value } if c.name == "frontend"])["API_HOST"] == "127.0.0.1" &&
      one([for c in jsondecode(aws_ecs_task_definition.app.container_definitions) : { for e in c.environment : e.name => e.value } if c.name == "frontend"])["API_PORT"] == "8080" &&
      alltrue([for c in jsondecode(aws_ecs_task_definition.app.container_definitions) :
        { for e in c.environment : e.name => e.value }["CLOUD_PROVIDER"] == "aws" &&
        { for e in c.environment : e.name => e.value }["APP_ENV"] == "staging"
      ]) &&
      one([for c in jsondecode(aws_ecs_task_definition.app.container_definitions) : one(c.portMappings).containerPort if c.name == "backend"]) == 8080,
      false
    )
    error_message = "Node FE must call the same-task BE at 127.0.0.1:8080 and the bundle must identify aws/staging."
  }
  assert {
    condition = try(
      contains(one([for c in jsondecode(aws_ecs_task_definition.app.container_definitions) : c.dependsOn if c.name == "frontend"]), { containerName = "backend", condition = "HEALTHY" }) &&
      length(one([for c in jsondecode(aws_ecs_task_definition.app.container_definitions) : c.healthCheck.command if c.name == "backend"])) > 0,
      false
    )
    error_message = "FE startup must wait for a BE readiness health check, not merely for the process to start."
  }
  assert {
    condition = (
      one(aws_ecs_service.app.load_balancer).container_name == "frontend" &&
      one(aws_ecs_service.app.load_balancer).container_port == 3000 &&
      aws_lb_target_group.app.port == 3000 &&
      aws_vpc_security_group_ingress_rule.service_from_alb.from_port == 3000 &&
      aws_vpc_security_group_ingress_rule.service_from_alb.to_port == 3000 &&
      aws_vpc_security_group_ingress_rule.service_from_alb.referenced_security_group_id == aws_security_group.alb.id &&
      aws_vpc_security_group_egress_rule.alb_to_service.from_port == 3000 &&
      aws_vpc_security_group_egress_rule.alb_to_service.to_port == 3000 &&
      !one(aws_ecs_service.app.network_configuration).assign_public_ip
    )
    error_message = "ALB and its task security-group path must expose FE3000 only; BE8080 stays inside the private task."
  }
  assert {
    condition = (
      length(aws_vpc_security_group_ingress_rule.alb_from_scanner_public) == 1 &&
      aws_vpc_security_group_ingress_rule.alb_from_scanner_public["54.116.158.85/32"].cidr_ipv4 == "54.116.158.85/32" &&
      length(aws_vpc_security_group_ingress_rule.alb_from_scanner_cidr) == 0 &&
      length(aws_vpc_security_group_ingress_rule.alb_from_scanner_sg) == 0 &&
      output.tool_config.dynamic_scanner_access
    )
    error_message = "Dual mode must keep scanner NAT /32 access and dynamic scanner management."
  }
  assert {
    condition = try(
      output.tool_config.container_name == "frontend" &&
      output.tool_config.backend_container_name == "backend",
      false
    )
    error_message = "tool_config must tell the provider which FE and BE containers to replace together."
  }
  assert {
    condition = alltrue([for c in jsondecode(aws_ecs_task_definition.app.container_definitions) :
      alltrue([for e in c.environment : !contains(["DATABASE_URL", "DB_PASSWORD", "RESET_DATABASE_URL"], e.name)]) &&
      (c.name != "frontend" || length(try(c.secrets, [])) == 0)
    ])
    error_message = "Never embed DB credentials in Terraform environment values or inject DB secrets into FE."
  }
  assert {
    condition = try(
      can(regex("^arn:aws:secretsmanager:ap-northeast-2:444455556666:secret:",
        one([for c in jsondecode(aws_ecs_task_definition.app.container_definitions) :
          one([for secret in c.secrets : secret.valueFrom if secret.name == "DATABASE_URL"])
        if c.name == "backend"])
      )) &&
      alltrue([for c in jsondecode(aws_ecs_task_definition.app.container_definitions) :
        alltrue([for secret in try(c.secrets, []) : !startswith(secret.name, "RESET_")])
      ]),
      false
    )
    error_message = "BE needs a same-account Secrets Manager DATABASE_URL reference, and app tasks must not receive reset credentials."
  }
}

# AWS has no DB/reset resources yet. Check the existing exported target contract
# without referencing nonexistent resources (which would be a configuration error).
# This does NOT prove RDS engine/network/IAM or reset SQL execution: those need follow-up checks.
# Runtime evidence must separately establish server major 18, schema readiness,
# zero remaining app rows and a nonzero reset exit on failure; plan is not that proof.
run "dual_staging_exports_separate_database_reset_contract" {
  command = plan
  variables {
    dual_container = true
  }
  assert {
    condition = try(
      output.tool_config.environment == "staging" &&
      output.tool_config.expected_database_major == 18 &&
      output.tool_config.database_name == "board_staging" &&
      output.tool_config.reset_task_family != output.tool_config.task_family &&
      startswith(output.tool_config.reset_task_family, "delta-lab-staging-") &&
      output.tool_config.backend_container_name == "backend",
      false
    )
    error_message = "AWS needs a staging-only DB18 target and a distinct one-off reset_task_family; absent RDS/reset support must not look ready."
  }
}
