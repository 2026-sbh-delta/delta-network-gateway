# The internal ALB VPC Origin requires an IGW attachment, without internet routes.
resource "aws_internet_gateway" "ingress" {
  vpc_id     = var.ingress_vpc_id
  tags       = { Name = "${local.alb_name}-vpc-origin" }
  depends_on = [terraform_data.contract]
}
resource "aws_vpc_security_group_ingress_rule" "alb_bootstrap" {
  count             = var.bootstrap_origin_access ? 1 : 0
  security_group_id = aws_security_group.alb.id
  prefix_list_id    = data.aws_ec2_managed_prefix_list.cloudfront.id
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  description       = "Temporary CloudFront ALB VPC Origin bootstrap"
}
resource "aws_cloudfront_vpc_origin" "alb" {
  vpc_origin_endpoint_config {
    name                   = "${local.prefix}-alb"
    arn                    = aws_lb.alb.arn
    http_port              = 80
    https_port             = 443
    origin_protocol_policy = "http-only"
    origin_ssl_protocols {
      items    = ["TLSv1.2"]
      quantity = 1
    }
  }
  tags       = { Name = "${local.prefix}-alb" }
  depends_on = [aws_internet_gateway.ingress, aws_vpc_security_group_ingress_rule.alb_bootstrap, aws_lb_listener_rule.test]
  timeouts {
    create = "30m"
    update = "30m"
    delete = "30m"
  }
}
resource "aws_vpc_security_group_ingress_rule" "alb_cloudfront_service" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = data.aws_security_group.alb_cloudfront_service.id
  ip_protocol                  = "tcp"
  from_port                    = 80
  to_port                      = 80
  description                  = "CloudFront service SG to internal ALB only"
}
