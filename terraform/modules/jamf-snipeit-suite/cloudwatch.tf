# =============================================================================
# CloudWatch — Log Group + Optional Alarm
# =============================================================================

resource "aws_cloudwatch_log_group" "ecs" {
  name              = "/ecs/${local.full_name}"
  retention_in_days = var.log_retention_days

  tags = merge(local.common_tags, { Name = "${local.full_name}-logs" })
}

# -- Optional: SNS topic for alarm notifications --
resource "aws_sns_topic" "alarms" {
  count = var.alarm_email != "" ? 1 : 0
  name  = "${local.full_name}-alarms"
  tags  = merge(local.common_tags, { Name = "${local.full_name}-alarms" })
}

resource "aws_sns_topic_subscription" "alarm_email" {
  count     = var.alarm_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alarms[0].arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# -- Alarm: ECS task failures --
resource "aws_cloudwatch_metric_alarm" "task_failures" {
  count = var.alarm_email != "" ? 1 : 0

  alarm_name          = "${local.full_name}-task-failures"
  alarm_description   = "ECS task exited with non-zero code"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "TaskFailures"
  namespace           = "ECS/ContainerInsights"
  period              = 86400  # 24 hours (task runs daily)
  statistic           = "Sum"
  threshold           = 1
  treat_missing_data  = "notBreaching"

  dimensions = {
    ClusterName = aws_ecs_cluster.main.name
  }

  alarm_actions = [aws_sns_topic.alarms[0].arn]

  tags = merge(local.common_tags, { Name = "${local.full_name}-task-alarm" })
}
