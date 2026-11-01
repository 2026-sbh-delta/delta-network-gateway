resource "aws_cloudfront_origin_request_policy" "server" {
  name    = local.prefix
  comment = "Forward test query strings without cookies or viewer headers"
  cookies_config { cookie_behavior = "none" }
  headers_config { header_behavior = "none" }
  query_strings_config { query_string_behavior = "all" }
}
resource "aws_cloudfront_distribution" "server" {
  enabled             = true
  is_ipv6_enabled     = false
  comment             = "Central ANF allow and deny demo through private ALB"
  default_root_object = "index.html"
  price_class         = "PriceClass_200"
  http_version        = "http2"
  wait_for_deployment = true
  origin {
    domain_name              = aws_s3_bucket.site.bucket_regional_domain_name
    origin_id                = "private-static-site"
    origin_access_control_id = aws_cloudfront_origin_access_control.site.id
  }
  origin {
    domain_name         = aws_lb.alb.dns_name
    origin_id           = "inspected-alb-api"
    connection_attempts = 1
    connection_timeout  = 5
    vpc_origin_config {
      vpc_origin_id            = aws_cloudfront_vpc_origin.alb.id
      origin_read_timeout      = 20
      origin_keepalive_timeout = 5
    }
  }
  default_cache_behavior {
    target_origin_id       = "private-static-site"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    viewer_protocol_policy = "redirect-to-https"
    cache_policy_id        = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
    compress               = true
  }
  dynamic "ordered_cache_behavior" {
    for_each = ["/api/*", "/health"]
    content {
      path_pattern             = ordered_cache_behavior.value
      target_origin_id         = "inspected-alb-api"
      allowed_methods          = ["GET", "HEAD", "OPTIONS"]
      cached_methods           = ["GET", "HEAD"]
      viewer_protocol_policy   = "redirect-to-https"
      cache_policy_id          = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
      origin_request_policy_id = aws_cloudfront_origin_request_policy.server.id
      compress                 = true
    }
  }
  dynamic "custom_error_response" {
    for_each = toset([403, 404, 500, 502, 503, 504])
    content {
      error_code            = custom_error_response.value
      error_caching_min_ttl = 0
    }
  }
  restrictions {
    geo_restriction { restriction_type = "none" }
  }
  viewer_certificate { cloudfront_default_certificate = true }
  tags       = { Name = local.prefix }
  depends_on = [aws_vpc_security_group_ingress_rule.alb_cloudfront_service, aws_s3_object.site]
}
