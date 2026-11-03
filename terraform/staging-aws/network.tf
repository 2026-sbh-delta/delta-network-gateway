resource "aws_security_group" "alb" {
  name_prefix = "${local.prefix}-alb-"
  description = "DAST runner to the staging ALB only"
  vpc_id      = var.vpc_id
  tags        = { Name = "${local.prefix}-alb" }
  depends_on  = [terraform_data.contract]
}
resource "aws_vpc_security_group_ingress_rule" "alb_from_scanner_cidr" {
  for_each          = var.scanner_cidrs
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = each.value
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  description       = "DAST runner CIDR"
}
resource "aws_vpc_security_group_ingress_rule" "alb_from_scanner_public" {
  for_each          = var.scanner_public_cidrs
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = each.value
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  description       = "DAST runner public egress"
}
# The staging tool adds and removes the runner's current /32 itself (tag DeltaManaged=scanner).
# Standalone rule resources never remove rules Terraform does not own.
resource "aws_vpc_security_group_ingress_rule" "alb_from_scanner_sg" {
  for_each                     = var.scanner_security_group_ids
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = each.value
  ip_protocol                  = "tcp"
  from_port                    = 80
  to_port                      = 80
  description                  = "DAST runner security group"
}
resource "aws_vpc_security_group_egress_rule" "alb_to_service" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.service.id
  ip_protocol                  = "tcp"
  from_port                    = var.container_port
  to_port                      = var.container_port
  description                  = "Staging tasks"
}

resource "aws_security_group" "service" {
  name_prefix = "${local.prefix}-svc-"
  description = "Staging tasks reachable from the internal ALB only"
  vpc_id      = var.vpc_id
  tags        = { Name = "${local.prefix}-svc" }
}
resource "aws_vpc_security_group_ingress_rule" "service_from_alb" {
  security_group_id            = aws_security_group.service.id
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = var.container_port
  to_port                      = var.container_port
  description                  = "Internal ALB"
}
resource "aws_vpc_security_group_egress_rule" "service_to_endpoints" {
  count                        = var.create_vpc_endpoints ? 1 : 0
  security_group_id            = aws_security_group.service.id
  referenced_security_group_id = aws_security_group.endpoints[0].id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "ECR and CloudWatch Logs endpoints"
}
resource "aws_vpc_security_group_egress_rule" "service_to_s3" {
  count             = var.create_vpc_endpoints ? 1 : 0
  security_group_id = aws_security_group.service.id
  prefix_list_id    = data.aws_prefix_list.s3[0].id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  description       = "ECR image layers through the S3 gateway endpoint"
}

resource "aws_security_group" "endpoints" {
  count       = var.create_vpc_endpoints ? 1 : 0
  name_prefix = "${local.prefix}-vpce-"
  description = "Interface endpoints for staging tasks"
  vpc_id      = var.vpc_id
  tags        = { Name = "${local.prefix}-vpce" }
}
resource "aws_vpc_security_group_ingress_rule" "endpoints_from_service" {
  count                        = var.create_vpc_endpoints ? 1 : 0
  security_group_id            = aws_security_group.endpoints[0].id
  referenced_security_group_id = aws_security_group.service.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "Staging tasks"
}
# Private DNS on these endpoints applies to the whole VPC, so other hosts in it (e.g. the tooling host pulling
# its images from ECR) resolve ECR and Logs to the endpoints too and must be allowed here.
resource "aws_vpc_security_group_ingress_rule" "endpoints_from_vpc_clients" {
  for_each                     = var.create_vpc_endpoints ? var.endpoint_client_security_group_ids : toset([])
  security_group_id            = aws_security_group.endpoints[0].id
  referenced_security_group_id = each.value
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "Other hosts in the VPC"
}

resource "aws_vpc_endpoint" "interface" {
  for_each            = local.interface_endpoints
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${var.region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = sort(tolist(var.subnet_ids))
  security_group_ids  = [aws_security_group.endpoints[0].id]
  private_dns_enabled = true
  tags                = { Name = "${local.prefix}-${replace(each.key, ".", "-")}" }
}
resource "aws_vpc_endpoint" "s3" {
  count             = var.create_vpc_endpoints ? 1 : 0
  vpc_id            = var.vpc_id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = sort(tolist(var.route_table_ids))
  tags              = { Name = "${local.prefix}-s3" }
}
