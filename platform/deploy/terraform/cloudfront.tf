resource "aws_cloudfront_vpc_origin" "platform" {

  vpc_origin_endpoint_config {

    name                   = var.name
    arn                    = aws_instance.app.arn
    http_port              = 8088
    https_port             = 443
    origin_protocol_policy = "http-only"
    origin_ssl_protocols {
      items    = ["TLSv1.2"]
      quantity = 1
    }

  }
  depends_on = [aws_internet_gateway.platform, aws_vpc_security_group_ingress_rule.bootstrap]
  timeouts {
    create = "30m"
    update = "30m"
    delete = "30m"
  }

}
resource "aws_cloudfront_origin_request_policy" "platform" {

  name = var.name
  cookies_config {
    cookie_behavior = "whitelist"
    cookies {
      items = ["delta_session"]
    }
  }
  headers_config {

    header_behavior = "whitelist"
    headers {
      items = ["Origin", "Content-Type", "X-Platform-Request", "X-CSRF-Token", "Idempotency-Key"]
    }

  }
  query_strings_config {
    query_string_behavior = "all"
  }

}
resource "aws_cloudfront_distribution" "platform" {

  aliases             = var.custom_domain == null ? [] : [var.custom_domain]
  enabled             = true
  is_ipv6_enabled     = true
  http_version        = "http2"
  price_class         = "PriceClass_200"
  comment             = "Delta developer platform"
  wait_for_deployment = true
  origin {

    domain_name = aws_instance.app.private_dns
    origin_id   = "platform"
    vpc_origin_config {
      vpc_origin_id            = aws_cloudfront_vpc_origin.platform.id
      origin_read_timeout      = 30
      origin_keepalive_timeout = 5
    }

  }
  default_cache_behavior {

    target_origin_id         = "platform"
    allowed_methods          = ["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"]
    cached_methods           = ["GET", "HEAD"]
    viewer_protocol_policy   = "https-only"
    cache_policy_id          = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
    origin_request_policy_id = aws_cloudfront_origin_request_policy.platform.id
    compress                 = true

  }
  dynamic "custom_error_response" {

    for_each = toset([400, 403, 404, 500, 502, 503, 504])
    content {
      error_code            = custom_error_response.value
      error_caching_min_ttl = 0
    }

  }
  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }
  viewer_certificate {
    cloudfront_default_certificate = var.custom_domain == null
    acm_certificate_arn            = var.custom_domain_certificate_arn
    ssl_support_method             = var.custom_domain == null ? null : "sni-only"
    minimum_protocol_version       = var.custom_domain == null ? "TLSv1" : "TLSv1.2_2021"
  }
  lifecycle {
    precondition {
      condition     = (var.custom_domain == null) == (var.custom_domain_certificate_arn == null)
      error_message = "관리 도메인과 ACM 인증서 ARN은 함께 지정해야 합니다."
    }
    precondition {
      condition     = var.custom_domain_certificate_arn == null ? true : split(":", var.custom_domain_certificate_arn)[4] == var.account_id
      error_message = "CloudFront와 같은 계정의 ACM 인증서가 필요합니다."
    }
  }
  depends_on = [aws_vpc_security_group_ingress_rule.cloudfront]

}
