resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = local.prefix }
}

# 기본 보안 그룹의 규칙을 모두 지운다. 실수로 붙여도 아무 통신도 허용하지 않는다.
resource "aws_default_security_group" "main" {
  vpc_id  = aws_vpc.main.id
  ingress = []
  egress  = []
  tags    = { Name = "${local.prefix}-default-deny" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = local.prefix }
}

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.main.id
  cidr_block              = local.public_cidrs[count.index]
  availability_zone       = var.availability_zones[count.index]
  map_public_ip_on_launch = false
  tags                    = { Name = "${local.prefix}-public-${count.index}", Tier = "public" }
}

# 내부 ALB와 CloudFront VPC Origin ENI만 둔다. 인터넷 경로가 없다.
resource "aws_subnet" "alb" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = local.alb_cidrs[count.index]
  availability_zone = var.availability_zones[count.index]
  tags              = { Name = "${local.prefix}-alb-${count.index}", Tier = "alb" }
}

resource "aws_subnet" "service" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = local.service_cidr
  availability_zone = var.availability_zones[0]
  tags              = { Name = "${local.prefix}-service", Tier = "service" }
}

# 나가는 호출은 모두 이 고정 IP 하나로 나간다. 대상 쪽은 이 IP만 허용한다.
resource "aws_eip" "nat" {
  domain = "vpc"
  tags   = { Name = "${local.prefix}-nat" }
}

resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id
  tags          = { Name = local.prefix }
  depends_on    = [aws_internet_gateway.main]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = { Name = "${local.prefix}-public" }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "service" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }
  tags = { Name = "${local.prefix}-service" }
}

resource "aws_route_table_association" "service" {
  subnet_id      = aws_subnet.service.id
  route_table_id = aws_route_table.service.id
}

# 기본 경로가 없는 라우팅 테이블. VPC 안 통신만 된다.
resource "aws_route_table" "alb" {
  vpc_id = aws_vpc.main.id
  route  = []
  tags   = { Name = "${local.prefix}-alb" }
}

resource "aws_route_table_association" "alb" {
  count          = 2
  subnet_id      = aws_subnet.alb[count.index].id
  route_table_id = aws_route_table.alb.id
}

# ECR 이미지 레이어는 S3에서 받는다. NAT를 거치지 않게 게이트웨이 엔드포인트를 둔다(무료).
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.service.id]
  tags              = { Name = "${local.prefix}-s3" }
}

resource "aws_route53_zone" "internal" {
  name = local.internal_zone
  vpc {
    vpc_id = aws_vpc.main.id
  }
  comment = "플랫폼 서비스 사설 이름"
}

resource "aws_route53_record" "service" {
  for_each = module.host
  zone_id  = aws_route53_zone.internal.zone_id
  name     = "${each.key}.${local.internal_zone}"
  type     = "A"
  ttl      = 60
  records  = [each.value.private_ip]
}
