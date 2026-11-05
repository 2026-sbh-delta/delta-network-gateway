locals {
  dast_subnets = var.dast_sandbox == null ? {} : var.dast_sandbox.subnet_route_tables
  # The platform route table already owns a /32 return route to the tooling VM.
  dast_return_tables = toset([for id in values(local.dast_subnets) : id if id != var.platform_route_table_id])
}
data "aws_lb" "dast" {
  count = var.dast_sandbox == null ? 0 : 1
  arn   = var.dast_sandbox.alb_arn
}
data "aws_subnet" "dast" {
  for_each = local.dast_subnets
  id       = each.key
}
data "aws_route_table" "dast" {
  for_each       = local.dast_subnets
  route_table_id = each.value
}
resource "aws_route" "dast_to_alb" {
  for_each                  = local.dast_subnets
  route_table_id            = var.tooling_route_table_id
  destination_cidr_block    = data.aws_subnet.dast[each.key].cidr_block
  vpc_peering_connection_id = aws_vpc_peering_connection.sast.id
  lifecycle {
    precondition {
      condition = (
        data.aws_subnet.dast[each.key].vpc_id == data.aws_subnet.platform.vpc_id &&
        data.aws_route_table.dast[each.key].vpc_id == data.aws_subnet.platform.vpc_id &&
        contains([for association in data.aws_route_table.dast[each.key].associations : association.subnet_id], each.key)
      )
      error_message = "플랫폼 VPC의 ALB 서브넷에 실제 연결된 Route Table이어야 합니다."
    }
  }
}
resource "aws_route" "dast_return" {
  for_each                  = local.dast_return_tables
  route_table_id            = each.value
  destination_cidr_block    = "${data.aws_instance.tooling.private_ip}/32"
  vpc_peering_connection_id = aws_vpc_peering_connection.sast.id
}
resource "aws_vpc_security_group_ingress_rule" "dast_http" {
  count                        = var.dast_sandbox == null ? 0 : 1
  security_group_id            = one(data.aws_lb.dast[0].security_groups)
  referenced_security_group_id = var.tooling_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 80
  to_port                      = 80
  description                  = "DAST scanner to the private platform sandbox only"
  depends_on                   = [aws_route.dast_to_alb, aws_route.dast_return]
  lifecycle {
    precondition {
      condition = (
        data.aws_lb.dast[0].internal && data.aws_lb.dast[0].load_balancer_type == "application" &&
        data.aws_lb.dast[0].vpc_id == data.aws_subnet.platform.vpc_id &&
        data.aws_lb.dast[0].tags["ManagedBy"] == "terraform" &&
        startswith(data.aws_lb.dast[0].name, "delta-platform-") &&
        toset(data.aws_lb.dast[0].subnets) == toset(keys(local.dast_subnets))
      )
      error_message = "플랫폼 전용 내부 ALB와 정확히 같은 서브넷만 연결해야 합니다."
    }
  }
}
