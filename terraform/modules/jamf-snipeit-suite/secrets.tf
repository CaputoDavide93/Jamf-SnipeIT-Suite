# =============================================================================
# SSM Parameter Store — ALL secrets for ECS task
# =============================================================================
# Every sensitive value is stored in SSM SecureString and injected into the
# container at runtime via the ECS "secrets" mechanism. This means:
#   - Values are encrypted at rest (AWS KMS)
#   - Values are NOT visible in the ECS task definition or AWS Console
#   - Values are NOT logged in CloudTrail
#   - Only the ECS execution role can read them

# -- Jamf --
resource "aws_ssm_parameter" "jamf_base_url" {
  name  = "/${local.full_name}/jamf-base-url"
  type  = "SecureString"
  value = var.jamf_base_url
  tags  = merge(local.common_tags, { Name = "${local.full_name}-jamf-base-url" })
}

resource "aws_ssm_parameter" "jamf_username" {
  name  = "/${local.full_name}/jamf-username"
  type  = "SecureString"
  value = var.jamf_username
  tags  = merge(local.common_tags, { Name = "${local.full_name}-jamf-username" })
}

resource "aws_ssm_parameter" "jamf_password" {
  name  = "/${local.full_name}/jamf-password"
  type  = "SecureString"
  value = var.jamf_password
  tags  = merge(local.common_tags, { Name = "${local.full_name}-jamf-password" })
}

# -- Snipe-IT --
resource "aws_ssm_parameter" "snipeit_base_url" {
  name  = "/${local.full_name}/snipeit-base-url"
  type  = "SecureString"
  value = var.snipeit_base_url
  tags  = merge(local.common_tags, { Name = "${local.full_name}-snipeit-base-url" })
}

resource "aws_ssm_parameter" "snipeit_api_token" {
  name  = "/${local.full_name}/snipeit-api-token"
  type  = "SecureString"
  value = var.snipeit_api_token
  tags  = merge(local.common_tags, { Name = "${local.full_name}-snipeit-api-token" })
}

# -- Azure AD --
resource "aws_ssm_parameter" "azure_tenant_id" {
  name  = "/${local.full_name}/azure-tenant-id"
  type  = "SecureString"
  value = var.azure_tenant_id
  tags  = merge(local.common_tags, { Name = "${local.full_name}-azure-tenant-id" })
}

resource "aws_ssm_parameter" "azure_client_id" {
  name  = "/${local.full_name}/azure-client-id"
  type  = "SecureString"
  value = var.azure_client_id
  tags  = merge(local.common_tags, { Name = "${local.full_name}-azure-client-id" })
}

resource "aws_ssm_parameter" "azure_client_secret" {
  name  = "/${local.full_name}/azure-client-secret"
  type  = "SecureString"
  value = var.azure_client_secret
  tags  = merge(local.common_tags, { Name = "${local.full_name}-azure-client-secret" })
}

# -- Slack --
resource "aws_ssm_parameter" "slack_bot_token" {
  name  = "/${local.full_name}/slack-bot-token"
  type  = "SecureString"
  value = var.slack_bot_token
  tags  = merge(local.common_tags, { Name = "${local.full_name}-slack-bot-token" })
}

# -- HiBob --
resource "aws_ssm_parameter" "hibob_user_id" {
  name  = "/${local.full_name}/hibob-user-id"
  type  = "SecureString"
  value = var.hibob_service_user_id != "" ? var.hibob_service_user_id : "not-configured"
  tags  = merge(local.common_tags, { Name = "${local.full_name}-hibob-user-id" })
}

resource "aws_ssm_parameter" "hibob_token" {
  name  = "/${local.full_name}/hibob-token"
  type  = "SecureString"
  value = var.hibob_service_user_token != "" ? var.hibob_service_user_token : "not-configured"
  tags  = merge(local.common_tags, { Name = "${local.full_name}-hibob-token" })
}

# -- AI Resolver --
resource "aws_ssm_parameter" "ai_api_key" {
  name  = "/${local.full_name}/ai-api-key"
  type  = "SecureString"
  value = var.ai_api_key != "" ? var.ai_api_key : "not-configured"
  tags  = merge(local.common_tags, { Name = "${local.full_name}-ai-api-key" })
}
