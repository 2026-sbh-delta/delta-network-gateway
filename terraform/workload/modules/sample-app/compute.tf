resource "aws_instance" "app" {
  for_each                    = local.hosts
  ami                         = data.aws_ami.selected.id
  instance_type               = var.config.instance_type
  subnet_id                   = var.workload_subnets[var.instance_az_id].id
  associate_public_ip_address = false
  vpc_security_group_ids      = [aws_security_group.app[each.key].id]
  iam_instance_profile        = aws_iam_instance_profile.app[each.key].name
  user_data_replace_on_change = true
  user_data_base64 = base64gzip(templatefile("${path.module}/templates/bootstrap.sh.tftpl", {
    role         = each.key
    node_version = var.config.node_version
    node_sha256  = var.config.node_sha256
    runtime_config = base64encode(jsonencode(merge({
      ROLE    = each.key, REGION = var.region, HOST = "0.0.0.0", PORT = each.key == "frontend" ? "3000" : "8080",
      APP_ENV = "aws", API_ENDPOINT = "/api", ARTIFACT_BUCKET = aws_s3_bucket.artifacts.id
      }, each.key == "backend" ? {
      STORAGE_BACKEND = "postgres", PGHOST = aws_db_instance.app.address, PGPORT = "5432", PGDATABASE = "delta_notes",
      PGUSER          = "delta_notes_app", PGSSLMODE = "verify-full", PGSSLROOTCERT = "/etc/delta-sample/rds-ca.pem",
      APP_SECRET_ARN  = aws_secretsmanager_secret.app.arn
    } : {})))
    runtime_wrapper   = base64encode(file("${path.module}/templates/runtime.py"))
    release_installer = base64encode(file("${path.module}/templates/install-release.py"))
    log_config = base64encode(jsonencode({
      agent = { run_as_user = "root" }
      logs = { logs_collected = { files = { collect_list = [{
        file_path       = "/var/log/delta-sample/app.log", log_group_name = aws_cloudwatch_log_group.app[each.key].name,
        log_stream_name = "{instance_id}", timezone = "UTC"
      }] } } }
    }))
  }))
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }
  root_block_device {
    volume_type           = "gp3"
    volume_size           = 20
    encrypted             = true
    delete_on_termination = true
  }
  tags       = { Name = "${local.prefix}-${each.key}", ApplicationRole = each.key }
  depends_on = [terraform_data.contract, aws_route.app_egress, aws_iam_role_policy.app, aws_vpc_security_group_egress_rule.https]
}

resource "aws_lb" "app" {
  name                 = "${local.prefix}-alb"
  internal             = false
  preserve_host_header = true
  load_balancer_type   = "application"
  security_groups      = [aws_security_group.app["alb"].id]
  subnets              = [for az in sort(keys(aws_subnet.public)) : aws_subnet.public[az].id]
  depends_on           = [aws_route.public, aws_route_table_association.public]
}
resource "aws_lb_target_group" "frontend" {
  name        = "${local.prefix}-fe"
  vpc_id      = var.vpc_id
  target_type = "instance"
  protocol    = "HTTP"
  port        = 3000
  health_check {
    path                = "/healthz"
    matcher             = "200"
    interval            = 15
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}
resource "aws_lb_target_group_attachment" "frontend" {
  target_group_arn = aws_lb_target_group.frontend.arn
  target_id        = aws_instance.app["frontend"].id
  port             = 3000
}
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.app.arn
  protocol          = "HTTP"
  port              = 80
  default_action {
    type             = var.config.certificate_arn == null ? "forward" : "redirect"
    target_group_arn = var.config.certificate_arn == null ? aws_lb_target_group.frontend.arn : null
    dynamic "redirect" {
      for_each = var.config.certificate_arn == null ? [] : [1]
      content {
        protocol    = "HTTPS"
        port        = "443"
        status_code = "HTTP_301"
      }
    }
  }
}
resource "aws_lb_listener" "https" {
  count             = var.config.certificate_arn == null ? 0 : 1
  load_balancer_arn = aws_lb.app.arn
  protocol          = "HTTPS"
  port              = 443
  certificate_arn   = var.config.certificate_arn
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.frontend.arn
  }
}
