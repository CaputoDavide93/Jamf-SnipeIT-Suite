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
  validation {
    condition     = var.aws_region == "eu-west-1"
    error_message = "This project must be deployed to eu-west-1 only."
  }
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

variable "azure_leavers_group_id" {
  type    = string
  default = ""
}

variable "azure_disabled_group_id" {
  type    = string
  default = ""
}

variable "azure_starters_group_id" {
  type    = string
  default = ""
}

# -- Feature flags --
variable "rehire_detection_dry_run" {
  type        = bool
  description = "Keeps rehire detection in dry-run until explicitly disabled"
  default     = true
}

variable "mark_contractors" {
  type        = bool
  description = "Appends the 'Contractor (Azure AD)' marker during user enrichment"
  default     = true
}

variable "module_enabled_overrides" {
  type        = map(bool)
  description = "Per-module enabled overrides, keyed by canonical module name"
  default     = {}
  validation {
    condition = alltrue([
      for name in keys(var.module_enabled_overrides) : contains([
        "azure_starters", "user_enrichment", "peripherals_sync", "correction",
        "user_match", "snipe_to_jamf", "rehire_detection", "leavers",
        "model_sync", "cleanup", "username_standardize", "ai_audit",
        "reconciliation", "health_check", "pending_reconciliation",
        "jamf_location_cleanup", "monthly_digest", "wakeup",
      ], name)
    ])
    error_message = "module_enabled_overrides keys must be canonical module names."
  }
}

variable "module_dry_run_overrides" {
  type        = map(bool)
  description = "Per-module dry-run overrides, keyed by canonical module name"
  default     = {}
  validation {
    condition = alltrue([
      for name in keys(var.module_dry_run_overrides) : contains([
        "azure_starters", "user_enrichment", "peripherals_sync", "correction",
        "user_match", "snipe_to_jamf", "rehire_detection", "leavers",
        "model_sync", "cleanup", "username_standardize", "ai_audit",
        "reconciliation", "health_check", "pending_reconciliation",
        "jamf_location_cleanup", "monthly_digest", "wakeup",
      ], name)
    ])
    error_message = "module_dry_run_overrides keys must be canonical module names."
  }
}

variable "ai_audit_allow_external_pii" {
  type        = bool
  description = "Explicitly allow names, emails, serials, and IDs in external AI audit prompts"
  default     = false
}

variable "health_check_max_workers" {
  type        = number
  description = "Maximum concurrent Jamf detail requests during health checks"
  default     = 8
  validation {
    condition     = var.health_check_max_workers >= 1 && var.health_check_max_workers <= 20
    error_message = "health_check_max_workers must be between 1 and 20."
  }
}

variable "health_check_scan_error_ratio_threshold" {
  type        = number
  description = "Failed Jamf detail-request ratio that marks a health scan failed"
  default     = 0.1
  validation {
    condition = (
      var.health_check_scan_error_ratio_threshold >= 0 &&
      var.health_check_scan_error_ratio_threshold <= 1
    )
    error_message = "health_check_scan_error_ratio_threshold must be between 0 and 1."
  }
}

# -- Matching --
variable "matching_email_domain" {
  type    = string
  default = ""
}

variable "matching_skip_usernames" {
  type    = string
  default = "admin,shared,guest"
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
  # Default: Tue 17:00 UTC full-sync run
  default = "cron(0 17 ? * TUE *)"
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
