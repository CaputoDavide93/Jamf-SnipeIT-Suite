# =============================================================================
# SSM Parameter Store — Secrets for ECS task
# =============================================================================
# Sensitive values stored in SSM and injected into the container at runtime.
# ECS execution role has permission to read these parameters.

resource "aws_ssm_parameter" "jamf_password" {
  name  = "/${local.full_name}/jamf-password"
  type  = "SecureString"
  value = var.jamf_password
  tags  = merge(local.common_tags, { Name = "${local.full_name}-jamf-password" })
}

resource "aws_ssm_parameter" "snipeit_api_token" {
  name  = "/${local.full_name}/snipeit-api-token"
  type  = "SecureString"
  value = var.snipeit_api_token
  tags  = merge(local.common_tags, { Name = "${local.full_name}-snipeit-api-token" })
}

resource "aws_ssm_parameter" "azure_client_secret" {
  name  = "/${local.full_name}/azure-client-secret"
  type  = "SecureString"
  value = var.azure_client_secret
  tags  = merge(local.common_tags, { Name = "${local.full_name}-azure-client-secret" })
}

resource "aws_ssm_parameter" "slack_bot_token" {
  name  = "/${local.full_name}/slack-bot-token"
  type  = "SecureString"
  value = var.slack_bot_token
  tags  = merge(local.common_tags, { Name = "${local.full_name}-slack-bot-token" })
}

resource "aws_ssm_parameter" "hibob_token" {
  name  = "/${local.full_name}/hibob-token"
  type  = "SecureString"
  value = var.hibob_service_user_token
  tags  = merge(local.common_tags, { Name = "${local.full_name}-hibob-token" })
}

resource "aws_ssm_parameter" "ai_api_key" {
  name  = "/${local.full_name}/ai-api-key"
  type  = "SecureString"
  value = var.ai_api_key != "" ? var.ai_api_key : "not-configured"
  tags  = merge(local.common_tags, { Name = "${local.full_name}-ai-api-key" })
}
