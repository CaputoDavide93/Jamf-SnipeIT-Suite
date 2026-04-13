# =============================================================================
# S3 Bucket for AI Resolver Cache
# =============================================================================
# Persists AI resolver decisions across Fargate runs — avoids re-calling the
# LLM API for the same ambiguous users every day, keeping us under rate limits.

resource "aws_s3_bucket" "ai_cache" {
  bucket = "${local.full_name}-ai-cache-${data.aws_caller_identity.current.account_id}"

  tags = merge(local.common_tags, {
    Name = "${local.full_name}-ai-cache"
  })
}

resource "aws_s3_bucket_public_access_block" "ai_cache" {
  bucket                  = aws_s3_bucket.ai_cache.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "ai_cache" {
  bucket = aws_s3_bucket.ai_cache.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "ai_cache" {
  bucket = aws_s3_bucket.ai_cache.id
  rule {
    id     = "expire-old-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

# Grant the ECS task role read/write access to the cache bucket
resource "aws_iam_role_policy" "ecs_task_s3_cache" {
  name = "s3-ai-cache"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
      ]
      Resource = "${aws_s3_bucket.ai_cache.arn}/*"
    }, {
      Effect = "Allow"
      Action = ["s3:ListBucket"]
      Resource = aws_s3_bucket.ai_cache.arn
    }]
  })
}
