resource "aws_internet_gateway" "app" {
  vpc_id     = var.vpc_id
  tags       = { Name = "${local.prefix}-public" }
  depends_on = [terraform_data.contract]
}
resource "aws_subnet" "public" {
  for_each                = var.config.public_subnet_cidrs
  vpc_id                  = var.vpc_id
  availability_zone_id    = each.key
  cidr_block              = each.value
  map_public_ip_on_launch = false
  tags                    = { Name = "${local.prefix}-${each.key}-public" }
  depends_on              = [terraform_data.contract]
}
resource "aws_route_table" "public" {
  for_each = var.config.public_subnet_cidrs
  vpc_id   = var.vpc_id
  tags     = { Name = "${local.prefix}-${each.key}-public" }
}
resource "aws_route_table_association" "public" {
  for_each       = aws_subnet.public
  subnet_id      = each.value.id
  route_table_id = aws_route_table.public[each.key].id
}
resource "aws_route" "public" {
  for_each               = aws_route_table.public
  route_table_id         = each.value.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.app.id
}
resource "aws_eip" "nat" {
  domain = "vpc"
  tags   = { Name = "${local.prefix}-nat" }
}
resource "aws_nat_gateway" "app" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[var.instance_az_id].id
  tags          = { Name = "${local.prefix}-nat" }
  depends_on    = [aws_route.public, aws_route_table_association.public]
}
resource "aws_route" "app_egress" {
  route_table_id         = var.workload_subnets[var.instance_az_id].route_table_id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.app.id
}
resource "aws_subnet" "database" {
  for_each                = var.config.db_subnet_cidrs
  vpc_id                  = var.vpc_id
  availability_zone_id    = each.key
  cidr_block              = each.value
  map_public_ip_on_launch = false
  tags                    = { Name = "${local.prefix}-${each.key}-database" }
  depends_on              = [terraform_data.contract]
}
resource "aws_route_table" "database" {
  for_each = var.config.db_subnet_cidrs
  vpc_id   = var.vpc_id
  tags     = { Name = "${local.prefix}-${each.key}-database" }
}
resource "aws_route_table_association" "database" {
  for_each       = aws_subnet.database
  subnet_id      = each.value.id
  route_table_id = aws_route_table.database[each.key].id
}

resource "aws_security_group" "app" {
  for_each    = setunion(toset(["alb", "database"]), local.hosts)
  name_prefix = "${local.prefix}-${each.key}-"
  description = "Sample application ${each.key}; explicit service flows only"
  vpc_id      = var.vpc_id
  tags        = { Name = "${local.prefix}-${each.key}" }
}
resource "aws_vpc_security_group_ingress_rule" "public_http" {
  security_group_id = aws_security_group.app["alb"].id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}
resource "aws_vpc_security_group_ingress_rule" "public_https" {
  count             = var.config.certificate_arn == null ? 0 : 1
  security_group_id = aws_security_group.app["alb"].id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}
locals {
  flows = merge({
    frontend = { source = "alb", target = "frontend", port = 3000 }
    }, contains(local.hosts, "backend") ? {
    backend  = { source = "frontend", target = "backend", port = 8080 }
    database = { source = "backend", target = "database", port = 5432 }
    } : {}, local.compose ? {
    compose_database = { source = "frontend", target = "database", port = 5432 }
  } : {})
}
resource "aws_vpc_security_group_ingress_rule" "service" {
  for_each                     = local.flows
  security_group_id            = aws_security_group.app[each.value.target].id
  referenced_security_group_id = aws_security_group.app[each.value.source].id
  ip_protocol                  = "tcp"
  from_port                    = each.value.port
  to_port                      = each.value.port
}
resource "aws_vpc_security_group_egress_rule" "service" {
  for_each                     = local.flows
  security_group_id            = aws_security_group.app[each.value.source].id
  referenced_security_group_id = aws_security_group.app[each.value.target].id
  ip_protocol                  = "tcp"
  from_port                    = each.value.port
  to_port                      = each.value.port
}
resource "aws_vpc_security_group_ingress_rule" "ssh" {
  for_each                     = local.hosts
  security_group_id            = aws_security_group.app[each.key].id
  referenced_security_group_id = var.management_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 22
  to_port                      = 22
}
resource "aws_vpc_security_group_egress_rule" "management" {
  for_each                     = local.hosts
  security_group_id            = var.management_security_group_id
  referenced_security_group_id = aws_security_group.app[each.key].id
  ip_protocol                  = "tcp"
  from_port                    = 22
  to_port                      = 22
}
resource "aws_vpc_security_group_egress_rule" "https" {
  for_each          = local.hosts
  security_group_id = aws_security_group.app[each.key].id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}
