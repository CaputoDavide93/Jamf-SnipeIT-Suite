# =============================================================================
# Jamf-SnipeIT Suite — ECS Fargate Scheduled Task
# =============================================================================

locals {
  name_prefix = var.project_name
  full_name   = "${local.name_prefix}-${var.environment}"

  common_tags = merge(var.tags, {
    Application = "JamfSnipeITSuite"
    Environment = var.environment
    ManagedBy   = "terraform"
    Owner       = "Davide Caputo - TechOps"
  })
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# Use default VPC if none specified
data "aws_vpc" "default" {
  count   = var.vpc_id == "" ? 1 : 0
  default = true
}

data "aws_subnets" "default" {
  count = length(var.subnet_ids) == 0 ? 1 : 0
  filter {
    name   = "vpc-id"
    values = [var.vpc_id != "" ? var.vpc_id : data.aws_vpc.default[0].id]
  }
  filter {
    name   = "map-public-ip-on-launch"
    values = ["true"]
  }
}

locals {
  vpc_id     = var.vpc_id != "" ? var.vpc_id : data.aws_vpc.default[0].id
  subnet_ids = length(var.subnet_ids) > 0 ? var.subnet_ids : data.aws_subnets.default[0].ids
}
