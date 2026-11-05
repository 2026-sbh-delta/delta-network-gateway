resource "aws_lb" "internal" {
  name                       = "${local.prefix}-internal"
  internal                   = true
  load_balancer_type         = "application"
  subnets                    = aws_subnet.alb[*].id
  security_groups            = [aws_security_group.alb.id]
  drop_invalid_header_fields = true
  desync_mitigation_mode     = "strictest"
  idle_timeout               = 120
  enable_deletion_protection = false
  tags                       = { Name = "${local.prefix}-internal" }
}

resource "aws_lb_target_group" "app" {
  for_each             = local.public_apps
  name                 = "${local.prefix}-${each.key}"
  port                 = local.services[each.key].port
  protocol             = "HTTP"
  vpc_id               = aws_vpc.main.id
  target_type          = "instance"
  deregistration_delay = 10
  health_check {
    path                = each.value.health_path
    matcher             = "200"
    interval            = 15
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_target_group_attachment" "app" {
  for_each         = local.public_apps
  target_group_arn = aws_lb_target_group.app[each.key].arn
  target_id        = module.host[each.key].instance_id
  port             = local.services[each.key].port
}

# 헤더가 맞지 않으면 404. CloudFront 배포마다 X-Delta-App 헤더를 붙여 보낸다.
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.internal.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      message_body = "not found"
      status_code  = "404"
    }
  }
}

resource "aws_lb_listener_rule" "app" {
  for_each     = local.public_apps
  listener_arn = aws_lb_listener.http.arn
  priority     = each.value.priority
  condition {
    http_header {
      http_header_name = "X-Delta-App"
      values           = [each.key]
    }
  }
  dynamic "condition" {
    for_each = length(each.value.paths) > 0 ? [1] : []
    content {
      path_pattern {
        values = each.value.paths
      }
    }
  }
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app[each.key].arn
  }
}
