# CloudFront 기본 도메인으로 HTTPS를 받고, VPC Origin으로 내부 ALB에 붙는다. ALB는 인터넷에 노출되지 않는다.

data "aws_cloudfront_cache_policy" "disabled" {
  name = "Managed-CachingDisabled"
}

# 쿠키·헤더·쿼리를 모두 넘긴다(Host 포함). 앱이 자기 공개 주소로 Origin을 검사한다.
data "aws_cloudfront_origin_request_policy" "all_viewer" {
  name = "Managed-AllViewer"
}

data "aws_cloudfront_response_headers_policy" "security" {
  name = "Managed-SecurityHeadersPolicy"
}

resource "aws_cloudfront_vpc_origin" "alb" {
  vpc_origin_endpoint_config {
    name                   = "${local.prefix}-alb"
    arn                    = aws_lb.internal.arn
    http_port              = 80
    https_port             = 443
    origin_protocol_policy = "http-only"
    origin_ssl_protocols {
      items    = ["TLSv1.2"]
      quantity = 1
    }
  }
  timeouts {
    create = "30m"
    update = "30m"
    delete = "30m"
  }
  depends_on = [aws_internet_gateway.main]
}

resource "aws_cloudfront_distribution" "app" {
  for_each        = local.public_apps
  enabled         = true
  is_ipv6_enabled = true
  http_version    = "http2and3"
  price_class     = "PriceClass_200"
  comment         = "${local.prefix} ${each.key}"

  origin {
    domain_name = aws_lb.internal.dns_name
    origin_id   = "alb"
    # 뷰어가 같은 이름의 헤더를 보내도 CloudFront가 이 값으로 덮어쓴다.
    custom_header {
      name  = "X-Delta-App"
      value = each.key
    }
    vpc_origin_config {
      vpc_origin_id            = aws_cloudfront_vpc_origin.alb.id
      origin_read_timeout      = 60
      origin_keepalive_timeout = 5
    }
  }

  default_cache_behavior {
    target_origin_id           = "alb"
    allowed_methods            = ["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"]
    cached_methods             = ["GET", "HEAD"]
    viewer_protocol_policy     = "redirect-to-https"
    cache_policy_id            = data.aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id   = data.aws_cloudfront_origin_request_policy.all_viewer.id
    response_headers_policy_id = data.aws_cloudfront_response_headers_policy.security.id
    compress                   = true

    # 로그인 없는 대시보드 시험 화면은 허용한 IP만 통과시킨다.
    dynamic "function_association" {
      for_each = each.key == "dashboard" ? [aws_cloudfront_function.dashboard_allow.arn] : []
      content {
        event_type   = "viewer-request"
        function_arn = function_association.value
      }
    }
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

# 대시보드 시험 화면 접근 제한: 허용 대역 밖 IP는 CloudFront 에서 403.
# 대역은 [시작 주소(정수), 크기] 로 미리 계산해 함수에 넣는다.
locals {
  dashboard_nets = [for c in var.dashboard_allowed_cidrs : [
    sum([for i, o in split(".", cidrhost(c, 0)) : tonumber(o) * pow(256, 3 - i)]),
    pow(2, 32 - tonumber(split("/", c)[1])),
  ]]
}

resource "aws_cloudfront_function" "dashboard_allow" {
  name    = "${local.prefix}-dashboard-allow"
  runtime = "cloudfront-js-2.0"
  comment = "dashboard viewer IP allow list"
  publish = true
  code    = <<-JS
    var nets = ${jsonencode(local.dashboard_nets)};
    function handler(event) {
      var p = event.viewer.ip.split(".");
      if (p.length === 4) {
        var n = ((+p[0]) * 256 + (+p[1])) * 65536 + (+p[2]) * 256 + (+p[3]);
        for (var i = 0; i < nets.length; i++) {
          if (n >= nets[i][0] && n < nets[i][0] + nets[i][1]) { return event.request; }
        }
      }
      return { statusCode: 403, statusDescription: "Forbidden" };
    }
  JS
}
