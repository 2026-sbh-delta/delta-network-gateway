resource "aws_ecr_repository" "platform" {

  name                 = var.name
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }
  encryption_configuration {
    encryption_type = "AES256"
  }
  force_delete = false

}
resource "aws_s3_bucket" "backup" {
  bucket        = "${var.name}-artifacts-${var.account_id}"
  force_destroy = false
}
resource "aws_s3_bucket_public_access_block" "backup" {

  bucket                  = aws_s3_bucket.backup.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true

}
resource "aws_s3_bucket_ownership_controls" "backup" {

  bucket = aws_s3_bucket.backup.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }

}
resource "aws_s3_bucket_server_side_encryption_configuration" "backup" {

  bucket = aws_s3_bucket.backup.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }

}
resource "aws_s3_bucket_versioning" "backup" {

  bucket = aws_s3_bucket.backup.id
  versioning_configuration {
    status = "Enabled"
  }

}
resource "aws_s3_bucket_lifecycle_configuration" "backup" {

  bucket = aws_s3_bucket.backup.id
  rule {

    id     = "database-retention"
    status = "Enabled"
    filter {
      prefix = "database/"
    }
    expiration {
      days = 7
    }
    noncurrent_version_expiration {
      noncurrent_days = 7
    }

  }
  depends_on = [aws_s3_bucket_versioning.backup]

}
resource "aws_s3_bucket_policy" "backup" {

  bucket = aws_s3_bucket.backup.id
  policy = jsonencode({
    Version = "2012-10-17", Statement = [{
      Sid = "RequireTLS", Effect = "Deny", Principal = "*", Action = "s3:*", Resource = [aws_s3_bucket.backup.arn, "${aws_s3_bucket.backup.arn}/*"], Condition = {
        Bool = {
          "aws:SecureTransport" = "false"
        }
      }
    }]
  })

}
