resource "aws_s3_bucket" "site" {
  bucket_prefix = "${var.name}-${var.environment}-anf-demo-"
  tags          = { Name = "${local.alb_name}-site" }
}
resource "aws_s3_bucket_public_access_block" "site" {
  bucket                  = aws_s3_bucket.site.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_ownership_controls" "site" {
  bucket = aws_s3_bucket.site.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "site" {
  bucket = aws_s3_bucket.site.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
resource "aws_cloudfront_origin_access_control" "site" {
  name                              = "${local.prefix}-site"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}
resource "aws_s3_bucket_policy" "site" {
  bucket = aws_s3_bucket.site.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "CloudFrontDistributionOnly"
      Effect    = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.site.arn}/*"
      Condition = { StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.server.arn } }
    }]
  })
  depends_on = [aws_s3_bucket_public_access_block.site]
}
resource "aws_s3_object" "site" {
  for_each = {
    "index.html"        = "text/html; charset=utf-8"
    "verification.json" = "application/json"
  }
  bucket                 = aws_s3_bucket.site.id
  key                    = each.key
  source                 = "${path.module}/web/${each.key}"
  source_hash            = filemd5("${path.module}/web/${each.key}")
  content_type           = each.value
  cache_control          = "no-store"
  server_side_encryption = "AES256"
  depends_on             = [aws_s3_bucket_server_side_encryption_configuration.site, aws_s3_bucket_ownership_controls.site]
}
