# 보안 그룹 규칙은 필요한 연결만 하나씩 연다. 서비스 사이 연결은 main.tf의 links 목록이 전부다.

resource "aws_security_group" "alb" {
  name        = "${local.prefix}-alb"
  description = "Internal ALB: only CloudFront VPC Origin ENIs in the ALB subnets"
  vpc_id      = aws_vpc.main.id
  tags        = { Name = "${local.prefix}-alb" }
}

# CloudFront VPC Origin 트래픽은 서브넷 대역이 아니라 CloudFront가 VPC에 만드는 관리형 보안 그룹으로 식별된다.
# (ENI 대역 CIDR로 열면 504가 난다. 2026-11-05 실측) 이 그룹은 VPC Origin을 만들 때 생긴다.
data "aws_security_group" "cloudfront_vpc_origin" {
  vpc_id = aws_vpc.main.id
  filter {
    name   = "group-name"
    values = ["CloudFront-VPCOrigins-Service-SG"]
  }
  depends_on = [aws_cloudfront_vpc_origin.alb]
}

resource "aws_vpc_security_group_ingress_rule" "alb_from_cloudfront" {
  security_group_id            = aws_security_group.alb.id
  description                  = "HTTP from CloudFront VPC Origin"
  ip_protocol                  = "tcp"
  from_port                    = 80
  to_port                      = 80
  referenced_security_group_id = data.aws_security_group.cloudfront_vpc_origin.id
}

resource "aws_vpc_security_group_egress_rule" "alb_to_app" {
  for_each                     = local.public_apps
  security_group_id            = aws_security_group.alb.id
  description                  = "ALB to ${each.key}"
  ip_protocol                  = "tcp"
  from_port                    = local.services[each.key].port
  to_port                      = local.services[each.key].port
  referenced_security_group_id = aws_security_group.host[each.key].id
}

resource "aws_vpc_security_group_ingress_rule" "app_from_alb" {
  for_each                     = local.public_apps
  security_group_id            = aws_security_group.host[each.key].id
  description                  = "${each.key} from ALB"
  ip_protocol                  = "tcp"
  from_port                    = local.services[each.key].port
  to_port                      = local.services[each.key].port
  referenced_security_group_id = aws_security_group.alb.id
}

resource "aws_vpc_security_group_egress_rule" "link_out" {
  for_each                     = local.link_map
  security_group_id            = aws_security_group.host[each.value.from].id
  description                  = "${each.value.from} to ${each.value.to}:${each.value.port}"
  ip_protocol                  = "tcp"
  from_port                    = each.value.port
  to_port                      = each.value.port
  referenced_security_group_id = aws_security_group.host[each.value.to].id
}

resource "aws_vpc_security_group_ingress_rule" "link_in" {
  for_each                     = local.link_map
  security_group_id            = aws_security_group.host[each.value.to].id
  description                  = "${each.value.to}:${each.value.port} from ${each.value.from}"
  ip_protocol                  = "tcp"
  from_port                    = each.value.port
  to_port                      = each.value.port
  referenced_security_group_id = aws_security_group.host[each.value.from].id
}

moved {
  from = aws_vpc_security_group_egress_rule.dast_to_sast
  to   = aws_vpc_security_group_egress_rule.link_out["dast>sast:8790"]
}

moved {
  from = aws_vpc_security_group_ingress_rule.sast_from_dast
  to   = aws_vpc_security_group_ingress_rule.link_in["dast>sast:8790"]
}

# 나가는 HTTPS: ECR·SSM·LLM API·GitHub·클라우드 API. 모두 NAT 고정 IP로 나간다.
resource "aws_vpc_security_group_egress_rule" "https_out" {
  for_each          = local.services
  security_group_id = aws_security_group.host[each.key].id
  description       = "HTTPS out via NAT"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

# 스테이징 진입점은 HTTP(80)다. DAST(점검)와 Jenkins(스테이징 도구의 준비 확인)만 연다.
resource "aws_vpc_security_group_egress_rule" "http_out" {
  for_each          = toset(["dast", "jenkins"])
  security_group_id = aws_security_group.host[each.key].id
  description       = "HTTP to staging entry points"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = "0.0.0.0/0"
}

moved {
  from = aws_vpc_security_group_egress_rule.dast_http_out
  to   = aws_vpc_security_group_egress_rule.http_out["dast"]
}
