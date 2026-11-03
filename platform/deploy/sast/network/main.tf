data "aws_subnet" "platform" { id = data.aws_instance.platform.subnet_id }
data "aws_subnet" "tooling" { id = data.aws_instance.tooling.subnet_id }
data "aws_instance" "platform" { instance_id = var.platform_instance_id }
data "aws_instance" "tooling" { instance_id = var.tooling_instance_id }
data "aws_route_table" "platform" { route_table_id = var.platform_route_table_id }
data "aws_route_table" "tooling" { route_table_id = var.tooling_route_table_id }

resource "aws_vpc_peering_connection" "sast" {
  vpc_id      = data.aws_subnet.platform.vpc_id
  peer_vpc_id = data.aws_subnet.tooling.vpc_id
  auto_accept = true
  tags        = { Name = "delta-platform-sast" }
  lifecycle {
    precondition {
      condition = (
        data.aws_subnet.platform.vpc_id != data.aws_subnet.tooling.vpc_id &&
        data.aws_route_table.platform.vpc_id == data.aws_subnet.platform.vpc_id &&
        data.aws_route_table.tooling.vpc_id == data.aws_subnet.tooling.vpc_id &&
        contains(data.aws_instance.platform.vpc_security_group_ids, var.platform_security_group_id) &&
        contains(data.aws_instance.tooling.vpc_security_group_ids, var.tooling_security_group_id)
      )
      error_message = "서로 다른 VPC의 VM·Route Table·SG 조합을 확인하세요."
    }
  }
}
resource "aws_route" "to_sast" {
  route_table_id            = var.platform_route_table_id
  destination_cidr_block    = "${data.aws_instance.tooling.private_ip}/32"
  vpc_peering_connection_id = aws_vpc_peering_connection.sast.id
}
resource "aws_route" "return_to_platform" {
  route_table_id            = var.tooling_route_table_id
  destination_cidr_block    = "${data.aws_instance.platform.private_ip}/32"
  vpc_peering_connection_id = aws_vpc_peering_connection.sast.id
}
resource "aws_vpc_security_group_ingress_rule" "sast_https" {
  security_group_id            = var.tooling_security_group_id
  referenced_security_group_id = var.platform_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "Private SAST HTTPS from the platform only"
  depends_on                   = [aws_vpc_peering_connection.sast]
}
