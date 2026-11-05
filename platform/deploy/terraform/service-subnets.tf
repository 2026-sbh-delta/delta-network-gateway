locals {
  service_cidrs = {
    jenkins_controller = "10.80.10.0/24"
    jenkins_agent      = "10.80.11.0/24"
    ai                 = "10.80.20.0/24"
    sast               = "10.80.30.0/24"
    dast               = "10.80.40.0/24"
  }
}

resource "aws_subnet" "service" {
  for_each                = local.service_cidrs
  vpc_id                  = aws_vpc.platform.id
  cidr_block              = each.value
  availability_zone       = var.availability_zone
  map_public_ip_on_launch = false
  tags                    = { Name = "${var.name}-${replace(each.key, "_", "-")}" }
}
resource "aws_route_table" "service" {
  for_each = local.service_cidrs
  vpc_id   = aws_vpc.platform.id
  tags     = { Name = "${var.name}-${replace(each.key, "_", "-")}" }
}
resource "aws_route_table_association" "service" {
  for_each       = local.service_cidrs
  subnet_id      = aws_subnet.service[each.key].id
  route_table_id = aws_route_table.service[each.key].id
}
resource "aws_route" "service_nat" {
  for_each               = local.service_cidrs
  route_table_id         = aws_route_table.service[each.key].id
  destination_cidr_block = "0.0.0.0/0"
  network_interface_id   = aws_instance.nat.primary_network_interface_id
}
resource "aws_vpc_security_group_ingress_rule" "service_nat" {
  for_each          = local.service_cidrs
  security_group_id = aws_security_group.nat.id
  cidr_ipv4         = each.value
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  description       = "HTTPS egress from ${each.key} subnet"
}
