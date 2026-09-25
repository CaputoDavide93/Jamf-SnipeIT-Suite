# =============================================================================
# ECR Repository — Docker image storage
# =============================================================================

resource "aws_ecr_repository" "app" {
  name                 = local.full_name
  image_tag_mutability = "MUTABLE"
  force_delete         = var.environment != "prod"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = merge(local.common_tags, {
    Name = "${local.full_name}-ecr"
  })
}

# Lifecycle policy — keep last 10 images, expire untagged after 7 days
resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Protect :base (weekly patch job builds FROM it)"
        selection    = { tagStatus = "tagged", tagPrefixList = ["base"], countType = "imageCountMoreThan", countNumber = 1 }
        action       = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Protect :latest"
        selection    = { tagStatus = "tagged", tagPrefixList = ["latest"], countType = "imageCountMoreThan", countNumber = 1 }
        action       = { type = "expire" }
      },
      {
        rulePriority = 3
        description  = "Expire untagged images after 7 days"
        selection    = { tagStatus = "untagged", countType = "sinceImagePushed", countUnit = "days", countNumber = 7 }
        action       = { type = "expire" }
      },
      {
        rulePriority = 4
        description  = "Keep last 10 images"
        selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 10 }
        action       = { type = "expire" }
      },
    ]
  })
}
