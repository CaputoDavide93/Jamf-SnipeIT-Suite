# =============================================================================
# ECS — Cluster, Task Definition, Security Group
# =============================================================================

resource "aws_ecs_cluster" "main" {
  name = local.full_name

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = merge(local.common_tags, { Name = "${local.full_name}-cluster" })
}

# Security group — egress only (no inbound needed)
resource "aws_security_group" "ecs_task" {
  name_prefix = "${local.full_name}-ecs-"
  vpc_id      = local.vpc_id
  description = "Jamf-SnipeIT Suite ECS task — outbound API calls only"

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow all outbound (Jamf, Snipe-IT, Azure, Slack, HiBob APIs)"
  }

  tags = merge(local.common_tags, { Name = "${local.full_name}-ecs-sg" })

  lifecycle {
    create_before_destroy = true
  }
}

# Task definition
resource "aws_ecs_task_definition" "app" {
  family                   = local.full_name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.container_cpu
  memory                   = var.container_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name      = "app"
    image     = "${aws_ecr_repository.app.repository_url}:latest"
    essential = true

    environment = [
      { name = "RUN_MODE", value = "run-once" },
      { name = "TZ", value = "Europe/London" },
      { name = "JAMF_BASE_URL", value = var.jamf_base_url },
      { name = "JAMF_USERNAME", value = var.jamf_username },
      { name = "SNIPEIT_BASE_URL", value = var.snipeit_base_url },
      { name = "AZURE_TENANT_ID", value = var.azure_tenant_id },
      { name = "AZURE_CLIENT_ID", value = var.azure_client_id },
      { name = "SLACK_CHANNEL_ID", value = var.slack_channel_id },
      { name = "HIBOB_SERVICE_USER_ID", value = var.hibob_service_user_id },
    ]

    secrets = [
      { name = "JAMF_PASSWORD", valueFrom = aws_ssm_parameter.jamf_password.arn },
      { name = "SNIPEIT_API_TOKEN", valueFrom = aws_ssm_parameter.snipeit_api_token.arn },
      { name = "AZURE_CLIENT_SECRET", valueFrom = aws_ssm_parameter.azure_client_secret.arn },
      { name = "SLACK_BOT_TOKEN", valueFrom = aws_ssm_parameter.slack_bot_token.arn },
      { name = "HIBOB_SERVICE_USER_TOKEN", valueFrom = aws_ssm_parameter.hibob_token.arn },
      { name = "AI_API_KEY", valueFrom = aws_ssm_parameter.ai_api_key.arn },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.ecs.name
        "awslogs-region"        = data.aws_region.current.name
        "awslogs-stream-prefix" = "ecs"
      }
    }

    healthCheck = {
      command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8080/health', timeout=5)\" || exit 1"]
      interval    = 60
      timeout     = 10
      startPeriod = 120
      retries     = 3
    }
  }])

  tags = merge(local.common_tags, { Name = "${local.full_name}-task" })
}
