data "aws_subnet" "platform" { id = var.platform_private_subnet_id }
data "aws_vpc" "platform" { id = data.aws_subnet.platform.vpc_id }
data "aws_instance" "nat" { instance_id = var.nat_instance_id }
data "aws_network_interface" "nat" { id = data.aws_instance.nat.network_interface_id }

resource "aws_subnet" "alb" {
  vpc_id                  = data.aws_vpc.platform.id
  cidr_block              = "10.80.2.0/24"
  availability_zone       = var.second_az
  map_public_ip_on_launch = false
  tags                    = { Name = "${local.name}-alb" }
  lifecycle {
    precondition {
      condition     = data.aws_vpc.platform.cidr_block == "10.80.0.0/16" && data.aws_subnet.platform.cidr_block == "10.80.1.0/24" && data.aws_subnet.platform.availability_zone != var.second_az
      error_message = "기존 플랫폼 대역과 서로 다른 두 AZ를 확인하세요."
    }
  }
}
resource "aws_subnet" "task" {
  vpc_id                  = data.aws_vpc.platform.id
  cidr_block              = "10.80.3.0/24"
  availability_zone       = data.aws_subnet.platform.availability_zone
  map_public_ip_on_launch = false
  tags                    = { Name = "${local.name}-tasks" }
}
resource "aws_route_table" "alb" { vpc_id = data.aws_vpc.platform.id }
resource "aws_route_table_association" "alb" {
  subnet_id      = aws_subnet.alb.id
  route_table_id = aws_route_table.alb.id
}
resource "aws_route_table" "task" { vpc_id = data.aws_vpc.platform.id }
resource "aws_route_table_association" "task" {
  subnet_id      = aws_subnet.task.id
  route_table_id = aws_route_table.task.id
}
resource "aws_route" "nat" {
  route_table_id         = aws_route_table.task.id
  destination_cidr_block = "0.0.0.0/0"
  network_interface_id   = data.aws_instance.nat.network_interface_id
  lifecycle {
    precondition {
      condition     = data.aws_network_interface.nat.vpc_id == data.aws_vpc.platform.id && !data.aws_instance.nat.source_dest_check && length(data.aws_instance.nat.vpc_security_group_ids) == 1
      error_message = "플랫폼 VPC의 NAT EC2와 단일 보안 그룹이 필요합니다."
    }
  }
}
resource "aws_vpc_security_group_ingress_rule" "nat_https" {
  security_group_id = one(data.aws_instance.nat.vpc_security_group_ids)
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = aws_subnet.task.cidr_block
  description       = "Platform sandbox HTTPS through existing NAT"
}
resource "aws_security_group" "alb" {
  name_prefix = "${local.name}-alb-"
  description = "Internal sandbox ALB; no client ingress in runtime-check release"
  vpc_id      = data.aws_vpc.platform.id
}
resource "aws_security_group" "task" {
  name_prefix = "${local.name}-task-"
  description = "Sandbox containers accept traffic only from sandbox ALB"
  vpc_id      = data.aws_vpc.platform.id
}
resource "aws_vpc_security_group_ingress_rule" "task" {
  security_group_id            = aws_security_group.task.id
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 1
  to_port                      = 65535
}
resource "aws_vpc_security_group_egress_rule" "alb" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.task.id
  ip_protocol                  = "tcp"
  from_port                    = 1
  to_port                      = 65535
}
resource "aws_vpc_security_group_egress_rule" "task_https" {
  security_group_id = aws_security_group.task.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}
resource "aws_lb" "sandbox" {
  name               = local.name
  internal           = true
  load_balancer_type = "application"
  subnets            = [data.aws_subnet.platform.id, aws_subnet.alb.id]
  security_groups    = [aws_security_group.alb.id]
}
resource "aws_lb_target_group" "sandbox" {
  name                 = local.name
  vpc_id               = data.aws_vpc.platform.id
  port                 = 3000
  protocol             = "HTTP"
  target_type          = "ip"
  deregistration_delay = 5
  health_check {
    path                = "/"
    port                = "traffic-port"
    interval            = 10
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 2
    matcher             = "200-399"
  }
  lifecycle { ignore_changes = [health_check[0].path] }
}
resource "aws_lb_listener" "sandbox" {
  load_balancer_arn = aws_lb.sandbox.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.sandbox.arn
  }
}
