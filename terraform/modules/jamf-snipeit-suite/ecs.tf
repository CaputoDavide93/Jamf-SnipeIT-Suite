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
  description = "Jamf-SnipeIT Suite ECS task - outbound API calls only"

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

# Task definition — ALL credentials injected from SSM at runtime
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

    # Non-sensitive configuration only
    environment = concat(
      [
        { name = "RUN_MODE", value = "run-once" },
        { name = "TZ", value = "Europe/London" },
        { name = "SLACK_CHANNEL_ID", value = var.slack_channel_id },
        { name = "AZURE_LEAVERS_GROUP_ID", value = var.azure_leavers_group_id },
        { name = "AZURE_DISABLED_GROUP_ID", value = var.azure_disabled_group_id },
        { name = "AZURE_STARTERS_GROUP_ID", value = var.azure_starters_group_id },
        { name = "MATCHING_EMAIL_DOMAIN", value = var.matching_email_domain },
        { name = "MATCHING_SKIP_USERNAMES", value = var.matching_skip_usernames },
        { name = "AI_CACHE_S3_BUCKET", value = aws_s3_bucket.ai_cache.id },
        { name = "AI_CACHE_S3_KEY", value = "ai-resolver-cache.json" },
        { name = "MODEL_SYNC_CATEGORY_ID", value = "2" },
        { name = "REHIRE_DETECTION_DRY_RUN", value = tostring(var.rehire_detection_dry_run) },
        { name = "MARK_CONTRACTORS", value = tostring(var.mark_contractors) },
        { name = "AI_AUDIT_ALLOW_EXTERNAL_PII", value = tostring(var.ai_audit_allow_external_pii) },
        { name = "HEALTH_CHECK_MAX_WORKERS", value = tostring(var.health_check_max_workers) },
        { name = "HEALTH_CHECK_SCAN_ERROR_RATIO_THRESHOLD", value = tostring(var.health_check_scan_error_ratio_threshold) },
      ],
      [
        for module_name, enabled in var.module_enabled_overrides : {
          name  = "MODULE_${upper(module_name)}_ENABLED"
          value = tostring(enabled)
        }
      ],
      [
        for module_name, dry_run in var.module_dry_run_overrides : {
          name  = "MODULE_${upper(module_name)}_DRY_RUN"
          value = tostring(dry_run)
        }
      ],
    )

    # ALL credentials injected from SSM SecureString at runtime
    # These are NOT visible in the task definition, console, or CloudTrail
    secrets = [
      { name = "JAMF_BASE_URL", valueFrom = aws_ssm_parameter.jamf_base_url.arn },
      { name = "JAMF_USERNAME", valueFrom = aws_ssm_parameter.jamf_username.arn },
      { name = "JAMF_PASSWORD", valueFrom = aws_ssm_parameter.jamf_password.arn },
      { name = "SNIPEIT_BASE_URL", valueFrom = aws_ssm_parameter.snipeit_base_url.arn },
      { name = "SNIPEIT_API_TOKEN", valueFrom = aws_ssm_parameter.snipeit_api_token.arn },
      { name = "AZURE_TENANT_ID", valueFrom = aws_ssm_parameter.azure_tenant_id.arn },
      { name = "AZURE_CLIENT_ID", valueFrom = aws_ssm_parameter.azure_client_id.arn },
      { name = "AZURE_CLIENT_SECRET", valueFrom = aws_ssm_parameter.azure_client_secret.arn },
      { name = "SLACK_BOT_TOKEN", valueFrom = aws_ssm_parameter.slack_bot_token.arn },
      { name = "HIBOB_SERVICE_USER_ID", valueFrom = aws_ssm_parameter.hibob_user_id.arn },
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
      command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz', timeout=5)\" || exit 1"]
      interval    = 60
      timeout     = 10
      startPeriod = 120
      retries     = 3
    }
  }])

  tags = merge(local.common_tags, { Name = "${local.full_name}-task" })
}
