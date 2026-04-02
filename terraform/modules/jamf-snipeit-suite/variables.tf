# =============================================================================
# Input Variables — Jamf-SnipeIT Suite ECS Fargate
# =============================================================================

variable "environment" {
  type        = string
  description = "Environment name (prod, sandbox)"
  validation {
    condition     = contains(["prod", "sandbox"], var.environment)
    error_message = "Environment must be 'prod' or 'sandbox'."
  }
}

variable "aws_region" {
  type    = string
  default = "eu-west-1"
}

variable "project_name" {
  type    = string
  default = "jamf-snipeit-suite"
}

# -- Networking --
variable "vpc_id" {
  type        = string
  description = "VPC ID for Fargate tasks (use default VPC if none specified)"
  default     = ""
}

variable "subnet_ids" {
  type        = list(string)
  description = "Subnet IDs for Fargate tasks (public subnets with internet access)"
  default     = []
}

# -- Container --
variable "container_cpu" {
  type        = number
  description = "Fargate task CPU units (256 = 0.25 vCPU)"
  default     = 256
}

variable "container_memory" {
  type        = number
  description = "Fargate task memory in MiB"
  default     = 512
}

# -- Secrets: Jamf --
variable "jamf_base_url" {
  type      = string
  sensitive = true
}

variable "jamf_username" {
  type      = string
  sensitive = true
}

variable "jamf_password" {
  type      = string
  sensitive = true
}

# -- Secrets: Snipe-IT --
variable "snipeit_base_url" {
  type      = string
  sensitive = true
}

variable "snipeit_api_token" {
  type      = string
  sensitive = true
}

# -- Secrets: Azure AD --
variable "azure_tenant_id" {
  type      = string
  sensitive = true
}

variable "azure_client_id" {
  type      = string
  sensitive = true
}

variable "azure_client_secret" {
  type      = string
  sensitive = true
}

# -- Secrets: Slack --
variable "slack_bot_token" {
  type      = string
  sensitive = true
}

variable "slack_channel_id" {
  type    = string
  default = ""
}

# -- Secrets: HiBob --
variable "hibob_service_user_id" {
  type      = string
  sensitive = true
  default   = ""
}

variable "hibob_service_user_token" {
  type      = string
  sensitive = true
  default   = ""
}

# -- Secrets: AI Resolver --
variable "ai_api_key" {
  type      = string
  sensitive = true
  default   = ""
}

# -- Schedule --
variable "schedule_expression" {
  type        = string
  description = "EventBridge cron/rate expression for the scheduled run"
  default     = "cron(0 6 * * ? *)"  # Daily 6am UTC
}

# -- Monitoring --
variable "alarm_email" {
  type        = string
  description = "Email for CloudWatch alarm notifications (empty = no alarms)"
  default     = ""
}

variable "log_retention_days" {
  type    = number
  default = 90
}

# -- Tags --
variable "tags" {
  type        = map(string)
  description = "Additional tags for all resources"
  default     = {}
}
