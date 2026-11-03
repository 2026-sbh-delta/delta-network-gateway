resource "aws_vpc" "platform" {

  cidr_block           = "10.80.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags = {
    Name = var.name
  }

}
resource "aws_internet_gateway" "platform" {
  vpc_id = aws_vpc.platform.id
}
resource "aws_subnet" "public" {

  vpc_id                  = aws_vpc.platform.id
  cidr_block              = "10.80.0.0/24"
  availability_zone       = var.availability_zone
  map_public_ip_on_launch = false
  tags = {
    Name = "${var.name}-nat"
  }

}
resource "aws_subnet" "private" {

  vpc_id                  = aws_vpc.platform.id
  cidr_block              = "10.80.1.0/24"
  availability_zone       = var.availability_zone
  map_public_ip_on_launch = false
  tags = {
    Name = "${var.name}-app"
  }

}
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.platform.id
}
resource "aws_route" "internet" {

  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.platform.id

}
resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.platform.id
}
resource "aws_route" "nat" {

  route_table_id         = aws_route_table.private.id
  destination_cidr_block = "0.0.0.0/0"
  network_interface_id   = aws_instance.nat.primary_network_interface_id

}
resource "aws_route_table_association" "private" {
  subnet_id      = aws_subnet.private.id
  route_table_id = aws_route_table.private.id
}
resource "aws_security_group" "app" {

  name_prefix = "${var.name}-app-"
  description = "CloudFront VPC origin only DB and SSH have no inbound rules"
  vpc_id      = aws_vpc.platform.id

}
resource "aws_vpc_security_group_egress_rule" "app_https" {

  security_group_id = aws_security_group.app.id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
  description       = "GitHub, ECR, S3, SSM and package downloads through NAT"

}
resource "aws_security_group" "nat" {

  name_prefix = "${var.name}-nat-"
  description = "HTTPS egress from platform private subnet"
  vpc_id      = aws_vpc.platform.id

}
resource "aws_vpc_security_group_ingress_rule" "nat_https" {

  security_group_id = aws_security_group.nat.id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = aws_subnet.private.cidr_block

}
resource "aws_vpc_security_group_egress_rule" "nat_https" {

  security_group_id = aws_security_group.nat.id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"

}
resource "aws_vpc_security_group_ingress_rule" "bootstrap" {

  count             = var.bootstrap_origin_access ? 1 : 0
  security_group_id = aws_security_group.app.id
  ip_protocol       = "tcp"
  from_port         = 8088
  to_port           = 8088
  prefix_list_id    = data.aws_ec2_managed_prefix_list.cloudfront.id

}
resource "aws_vpc_security_group_ingress_rule" "cloudfront" {

  security_group_id            = aws_security_group.app.id
  ip_protocol                  = "tcp"
  from_port                    = 8088
  to_port                      = 8088
  referenced_security_group_id = data.aws_security_group.cloudfront.id

}
