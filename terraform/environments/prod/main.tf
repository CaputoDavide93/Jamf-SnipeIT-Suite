# =============================================================================
# Production Environment — Jamf-SnipeIT Suite
# =============================================================================
# ECS Fargate scheduled task that runs all sync modules daily.
#
# Usage:
#   cd terraform/environments/prod
#   cp terraform.tfvars.example terraform.tfvars   # fill in secrets
#   terraform init
#   terraform plan
#   terraform apply
# =============================================================================

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    bucket         = "jamf-snipeit-terraform-state-<AWS_ACCOUNT_ID>"
    key            = "jamf-snipeit-suite/terraform.tfstate"
    region         = "eu-west-1"
    dynamodb_table = "jamf-snipeit-terraform-locks"
    encrypt        = true
  }
}

provider "aws" {
  region = var.aws_region

  # SAFETY: Only deploy to THIS account and region — prevents accidental
  # deployment to wrong account if credentials/profile are misconfigured
  allowed_account_ids = ["<AWS_ACCOUNT_ID>"]

  default_tags {
    tags = {
      Application = "JamfSnipeITSuite"
      Environment = "prod"
      ManagedBy   = "terraform"
      Owner       = "Davide Caputo - TechOps"
    }
  }
}

# =============================================================================
# Call the shared module
# =============================================================================
module "jamf_snipeit_suite" {
  source = "../../modules/jamf-snipeit-suite"

  environment  = "prod"
  aws_region   = var.aws_region
  project_name = var.project_name

  # -- Jamf --
  jamf_base_url = var.jamf_base_url
  jamf_username = var.jamf_username
  jamf_password = var.jamf_password

  # -- Snipe-IT --
  snipeit_base_url  = var.snipeit_base_url
  snipeit_api_token = var.snipeit_api_token

  # -- Azure AD --
  azure_tenant_id         = var.azure_tenant_id
  azure_client_id         = var.azure_client_id
  azure_client_secret     = var.azure_client_secret
  azure_leavers_group_id  = var.azure_leavers_group_id
  azure_disabled_group_id = var.azure_disabled_group_id
  azure_starters_group_id = var.azure_starters_group_id

  # -- Matching --
  matching_email_domain   = var.matching_email_domain
  matching_skip_usernames = var.matching_skip_usernames

  # -- Feature flags --
  rehire_detection_dry_run = var.rehire_detection_dry_run
  mark_contractors         = var.mark_contractors

  # Optional production safety overrides, keyed by canonical module name.
  module_enabled_overrides                = var.module_enabled_overrides
  module_dry_run_overrides                = var.module_dry_run_overrides
  ai_audit_allow_external_pii             = var.ai_audit_allow_external_pii
  health_check_max_workers                = var.health_check_max_workers
  health_check_scan_error_ratio_threshold = var.health_check_scan_error_ratio_threshold

  # -- Slack --
  slack_bot_token  = var.slack_bot_token
  slack_channel_id = var.slack_channel_id

  # -- HiBob --
  hibob_service_user_id    = var.hibob_service_user_id
  hibob_service_user_token = var.hibob_service_user_token

  # -- AI Resolver --
  ai_api_key = var.ai_api_key

  # -- Schedule: daily 6am UTC (7am BST) --
  schedule_expression = "cron(0 17 ? * TUE *)" # Tue 17:00 UTC = 18:00 UK BST

  # -- Container sizing (0.25 vCPU, 512MB — plenty for API calls) --
  container_cpu    = 256
  container_memory = 512

  # -- Monitoring --
  alarm_email        = var.alarm_email
  log_retention_days = 90
}

# =============================================================================
# Variables
# =============================================================================
variable "aws_region" {
  type    = string
  default = "eu-west-1"
  validation {
    condition     = var.aws_region == "eu-west-1"
    error_message = "This project must be deployed to eu-west-1 only."
  }
}

variable "aws_profile" {
  type    = string
  default = "default"
}

variable "project_name" {
  type    = string
  default = "jamf-snipeit-suite"
}

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

variable "snipeit_base_url" {
  type      = string
  sensitive = true
}
variable "snipeit_api_token" {
  type      = string
  sensitive = true
}

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
variable "matching_email_domain" {
  type    = string
  default = ""
}
variable "matching_skip_usernames" {
  type    = string
  default = "admin,administrator,shared,guest,dan,createfuture,xdesign,payables,iossandboxaccount,iossandbox,sandbox,testuser,test"
}

variable "module_enabled_overrides" {
  type    = map(bool)
  default = {}
}

variable "rehire_detection_dry_run" {
  # Prod has run rehire detection live since task-def rev 6. The shared module
  # defaults this to true (safe for new environments); prod must keep its
  # codified live state or the next apply would silently revert to dry-run.
  type    = bool
  default = false
}

variable "mark_contractors" {
  type    = bool
  default = true
}

variable "module_dry_run_overrides" {
  type    = map(bool)
  default = {}
}

variable "ai_audit_allow_external_pii" {
  type    = bool
  default = false
}

variable "health_check_max_workers" {
  type    = number
  default = 8
}

variable "health_check_scan_error_ratio_threshold" {
  type    = number
  default = 0.1
}

variable "slack_bot_token" {
  type      = string
  sensitive = true
}
variable "slack_channel_id" {
  type    = string
  default = ""
}

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

variable "ai_api_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "alarm_email" {
  type    = string
  default = ""
}

# =============================================================================
# Outputs
# =============================================================================
output "ecr_repository_url" {
  description = "Push Docker images here"
  value       = module.jamf_snipeit_suite.ecr_repository_url
}

output "ecs_cluster_name" {
  value = module.jamf_snipeit_suite.ecs_cluster_name
}

output "log_group" {
  value = module.jamf_snipeit_suite.log_group_name
}

output "docker_push_commands" {
  description = "Run these to deploy a new image"
  value       = module.jamf_snipeit_suite.docker_push_commands
}
