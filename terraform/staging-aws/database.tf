# A separate staging-only DB in the existing VPC/subnets. No production DB or new VPC.
# 스테이징 DB 사용자 하나를 앱과 초기화 작업이 함께 쓴다. 배포마다 비우는 시험 데이터만 담는다.
# Terraform이 비밀번호를 만들고 비밀값까지 채운다(사람이 SQL·비밀값을 넣는 단계 없음).
resource "aws_db_subnet_group" "app" {
  count      = var.dual_container ? 1 : 0
  name       = "${local.prefix}-db"
  subnet_ids = sort(tolist(var.subnet_ids))
}
resource "aws_security_group" "database" {
  count       = var.dual_container ? 1 : 0
  name_prefix = "${local.prefix}-db-"
  description = "Staging PostgreSQL from staging tasks only"
  vpc_id      = var.vpc_id
}
resource "aws_vpc_security_group_ingress_rule" "database_from_tasks" {
  count                        = var.dual_container ? 1 : 0
  security_group_id            = aws_security_group.database[0].id
  referenced_security_group_id = aws_security_group.service.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}
resource "aws_vpc_security_group_egress_rule" "tasks_to_database" {
  count                        = var.dual_container ? 1 : 0
  security_group_id            = aws_security_group.service.id
  referenced_security_group_id = aws_security_group.database[0].id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}
resource "aws_db_parameter_group" "app" {
  count  = var.dual_container ? 1 : 0
  name   = "${local.prefix}-pg18"
  family = "postgres18"
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
}
resource "random_password" "db" {
  count   = var.dual_container ? 1 : 0
  length  = 32
  special = false
}
resource "aws_db_instance" "app" {
  count                       = var.dual_container ? 1 : 0
  identifier                  = "${local.prefix}-db"
  engine                      = "postgres"
  engine_version              = "18"
  instance_class              = "db.t4g.micro"
  allocated_storage           = 20
  storage_type                = "gp3"
  storage_encrypted           = true
  db_name                     = local.database_name
  username                    = local.database_user
  password                    = random_password.db[0].result
  multi_az                    = false
  publicly_accessible         = false
  db_subnet_group_name        = aws_db_subnet_group.app[0].name
  vpc_security_group_ids      = [aws_security_group.database[0].id]
  parameter_group_name        = aws_db_parameter_group.app[0].name
  backup_retention_period     = 0
  deletion_protection         = false
  skip_final_snapshot         = true
  auto_minor_version_upgrade  = true
  allow_major_version_upgrade = false
}

# 앱(BE)과 초기화 작업이 같이 읽는 접속 정보. CA는 AWS가 공개한 서울 리전 RDS 신뢰 묶음이다.
resource "aws_secretsmanager_secret" "runtime" {
  count                   = var.dual_container ? 1 : 0
  name_prefix             = "${local.prefix}/runtime-"
  description             = "Staging board_app JSON: DATABASE_URL, PGHOST, PGPASSWORD, PGSSLROOTCERT_PEM"
  recovery_window_in_days = 0
}
resource "aws_secretsmanager_secret_version" "runtime" {
  count     = var.dual_container ? 1 : 0
  secret_id = aws_secretsmanager_secret.runtime[0].id
  secret_string = jsonencode({
    DATABASE_URL      = "postgresql://${local.database_user}:${random_password.db[0].result}@${aws_db_instance.app[0].address}:5432/${local.database_name}"
    PGHOST            = aws_db_instance.app[0].address
    PGPASSWORD        = random_password.db[0].result
    PGSSLROOTCERT_PEM = file("${path.module}/rds-ca-${var.region}.pem")
  })
}
resource "aws_iam_role_policy" "runtime_database" {
  count = var.dual_container ? 1 : 0
  role  = aws_iam_role.execution.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = aws_secretsmanager_secret.runtime[0].arn
  }] })
}
resource "aws_vpc_endpoint" "secretsmanager" {
  count               = var.dual_container && var.create_vpc_endpoints ? 1 : 0
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${var.region}.secretsmanager"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = sort(tolist(var.subnet_ids))
  security_group_ids  = [aws_security_group.endpoints[0].id]
  private_dns_enabled = true
}

locals {
  app_environment = [
    { name = "APP_ENV", value = "staging" },
    { name = "CLOUD_PROVIDER", value = "aws" }
  ]
  database_environment = [
    { name = "STORAGE_BACKEND", value = "postgres" },
    { name = "PGDATABASE", value = local.database_name },
    { name = "PGUSER", value = local.database_user },
    { name = "PGPORT", value = "5432" },
    { name = "PGSSLMODE", value = "verify-full" },
    { name = "PGSSLROOTCERT", value = "/tmp/rds-ca.pem" },
    { name = "NODE_OPTIONS", value = "--import=data:text/javascript;base64,${base64encode(local.database_ca_prelude)}" }
  ]
  # The provider replaces reset entryPoint; NODE_OPTIONS survives that replacement.
  # This imports only a local prelude, writes the trusted public CA without logging,
  # and leaves the normal Node reset exit/status semantics intact.
  database_ca_prelude = <<-JS
    import { writeFileSync } from 'node:fs';
    if (!process.env.PGSSLROOTCERT_PEM) throw new Error('Missing trusted RDS CA');
    writeFileSync(process.env.PGSSLROOTCERT, process.env.PGSSLROOTCERT_PEM, { mode: 0o600 });
    delete process.env.PGSSLROOTCERT_PEM;
  JS
  runtime_database_secrets = var.dual_container ? [for key in ["DATABASE_URL", "PGHOST", "PGPASSWORD", "PGSSLROOTCERT_PEM"] : {
    name = key, valueFrom = "${aws_secretsmanager_secret.runtime[0].arn}:${key}::"
  }] : []
  app_logs = {
    logDriver = "awslogs"
    options = {
      awslogs-group         = aws_cloudwatch_log_group.app.name
      awslogs-region        = var.region
      awslogs-stream-prefix = "app"
    }
  }
}
resource "aws_ecs_task_definition" "db_reset" {
  count                    = var.dual_container ? 1 : 0
  family                   = "${local.prefix}-db-reset"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.cpu)
  memory                   = tostring(var.memory)
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }
  container_definitions = jsonencode([{
    name      = "reset"
    image     = var.bootstrap_backend_image
    essential = true
    command   = ["node", "scripts/reset-db.mjs"]
    environment = concat(local.app_environment, local.database_environment, [
      { name = "APP_RESET_DATABASE", value = local.database_name }
    ])
    secrets          = local.runtime_database_secrets
    logConfiguration = merge(local.app_logs, { options = merge(local.app_logs.options, { awslogs-stream-prefix = "reset" }) })
  }])
}

resource "aws_vpc_security_group_egress_rule" "tasks_existing_nat_https" {
  count             = var.dual_container && !var.create_vpc_endpoints && var.existing_nat_egress ? 1 : 0
  security_group_id = aws_security_group.service.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  description       = "Explicit existing NAT egress for ECR, Logs, S3 and Secrets Manager HTTPS"
}
