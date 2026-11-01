resource "aws_subnet" "alb" {
  for_each                        = var.alb_subnets
  vpc_id                          = var.ingress_vpc_id
  availability_zone_id            = each.key
  cidr_block                      = each.value
  map_public_ip_on_launch         = false
  assign_ipv6_address_on_creation = false
  tags                            = { Name = "${local.alb_name}-${each.key}" }
  depends_on                      = [terraform_data.contract]
}
resource "aws_route_table" "alb" {
  for_each = var.alb_subnets
  vpc_id   = var.ingress_vpc_id
  tags     = { Name = "${local.alb_name}-${each.key}" }
}
resource "aws_route_table_association" "alb" {
  for_each       = var.alb_subnets
  subnet_id      = aws_subnet.alb[each.key].id
  route_table_id = aws_route_table.alb[each.key].id
}
resource "aws_route" "alb_to_server" {
  for_each               = var.alb_subnets
  route_table_id         = aws_route_table.alb[each.key].id
  destination_cidr_block = data.aws_vpc.server.cidr_block
  transit_gateway_id     = data.aws_ec2_transit_gateway.central.id
}
resource "aws_security_group" "alb" {
  name_prefix = "${local.alb_name}-"
  description = "CloudFront private origin to inspected server test ports only"
  vpc_id      = var.ingress_vpc_id
  tags        = { Name = local.alb_name }
}
resource "aws_vpc_security_group_egress_rule" "alb" {
  for_each          = local.test_ports
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = "${var.expected_server_private_ip}/32"
  ip_protocol       = "tcp"
  from_port         = each.value
  to_port           = each.value
  description       = "ANF ${each.key} control"
}
resource "aws_vpc_security_group_ingress_rule" "server_from_alb" {
  for_each          = local.server_alb_rules
  security_group_id = var.server_security_group_id
  cidr_ipv4         = each.value.cidr
  ip_protocol       = "tcp"
  from_port         = each.value.port
  to_port           = each.value.port
  description       = "ALB test source; both ports admitted here, central ANF decides"
  depends_on        = [terraform_data.contract]
}
resource "aws_lb" "alb" {
  name                       = local.alb_name
  internal                   = true
  load_balancer_type         = "application"
  ip_address_type            = "ipv4"
  subnets                    = [for az in sort(keys(var.alb_subnets)) : aws_subnet.alb[az].id]
  security_groups            = [aws_security_group.alb.id]
  drop_invalid_header_fields = true
  idle_timeout               = 15
  tags                       = { Name = local.alb_name }
  depends_on                 = [aws_route_table_association.alb, aws_route.alb_to_server]
}
resource "aws_lb_target_group" "test" {
  for_each    = local.test_ports
  name        = "${local.alb_name}-${each.key}"
  port        = each.value
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.ingress_vpc_id
  health_check {
    enabled             = true
    port                = "8080"
    protocol            = "HTTP"
    path                = "/health"
    matcher             = "200"
    interval            = 10
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
  deregistration_delay = 5
  tags                 = { Name = "${local.alb_name}-${each.key}" }
}
resource "aws_lb_target_group_attachment" "server" {
  for_each          = local.test_ports
  target_group_arn  = aws_lb_target_group.test[each.key].arn
  target_id         = var.expected_server_private_ip
  port              = each.value
  availability_zone = "all"
}
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.alb.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type = "fixed-response"
    fixed_response {
      content_type = "application/json"
      message_body = "{\"message\":\"Unknown test path\"}"
      status_code  = "404"
    }
  }
}
resource "aws_lb_listener_rule" "test" {
  for_each     = local.test_ports
  listener_arn = aws_lb_listener.http.arn
  priority     = each.key == "allow" ? 10 : 20
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.test[each.key].arn
  }
  condition {
    path_pattern {
      values = each.key == "allow" ? ["/api/allow", "/api/allow/*", "/health"] : ["/api/deny", "/api/deny/*"]
    }
  }
}
