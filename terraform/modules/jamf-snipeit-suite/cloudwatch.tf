# =============================================================================
# CloudWatch — Log Group + Failure Alerts
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

# -- Alert: scheduled task failures --
# ECS/ContainerInsights publishes no "TaskFailures" metric, so a metric alarm
# on it never fires. Match the ECS task state change event instead: any task
# in this cluster that stops with a non-zero container exit code, or never
# starts (image pull, secrets, networking).
resource "aws_cloudwatch_event_rule" "task_failed" {
  name        = "${local.full_name}-task-failed"
  description = "Scheduled ECS task stopped with non-zero exit code or failed to start"

  event_pattern = jsonencode({
    source      = ["aws.ecs"]
    detail-type = ["ECS Task State Change"]
    detail = {
      clusterArn = [aws_ecs_cluster.main.arn]
      lastStatus = ["STOPPED"]
      "$or" = [
        { containers = { exitCode = [{ "anything-but" = 0 }] } },
        { stopCode = ["TaskFailedToStart"] },
      ]
    }
  })

  tags = merge(local.common_tags, { Name = "${local.full_name}-task-failed" })
}

# Static keys so for_each is known at plan time even before the email topic exists
locals {
  alert_topic_arns = merge(
    var.alerts_topic_arn != "" ? { shared = var.alerts_topic_arn } : {},
    var.alarm_email != "" ? { email = aws_sns_topic.alarms[0].arn } : {},
  )
}

resource "aws_cloudwatch_event_target" "task_failed_sns" {
  for_each = local.alert_topic_arns

  rule = aws_cloudwatch_event_rule.task_failed.name
  arn  = each.value

  input_transformer {
    input_paths = {
      group     = "$.detail.group"
      stopCode  = "$.detail.stopCode"
      reason    = "$.detail.stoppedReason"
      exitCode  = "$.detail.containers[0].exitCode"
      taskArn   = "$.detail.taskArn"
      stoppedAt = "$.detail.stoppedAt"
    }
    input_template = "\"${local.full_name}: ECS task <group> failed (exit <exitCode>, <stopCode>: <reason>) at <stoppedAt>. Task <taskArn>. Logs: ${aws_cloudwatch_log_group.ecs.name}\""
  }
}

# The optional email topic is owned here, so grant EventBridge publish on it.
# The shared alerts topic's policy is managed by whoever owns that topic.
resource "aws_sns_topic_policy" "alarms" {
  count = var.alarm_email != "" ? 1 : 0
  arn   = aws_sns_topic.alarms[0].arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowEventBridgePublish"
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sns:Publish"
      Resource  = aws_sns_topic.alarms[0].arn
      Condition = { ArnEquals = { "aws:SourceArn" = aws_cloudwatch_event_rule.task_failed.arn } }
    }]
  })
}

# -- Alarm: schedule could not launch a task --
# If RunTask itself fails (capacity, IAM, subnet) no task exists, so the rule
# above never sees it. EventBridge counts those as FailedInvocations.
resource "aws_cloudwatch_metric_alarm" "schedule_failed_invocations" {
  for_each = var.alerts_topic_arn != "" ? local.schedules : {}

  alarm_name          = "${local.full_name}-${each.key}-failed-invocations"
  alarm_description   = "EventBridge schedule ${each.key} failed to start the ECS task"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "FailedInvocations"
  namespace           = "AWS/Events"
  period              = 300
  statistic           = "Sum"
  threshold           = 1
  treat_missing_data  = "notBreaching"

  dimensions = {
    RuleName = aws_cloudwatch_event_rule.scheduled[each.key].name
  }

  alarm_actions = [var.alerts_topic_arn]

  tags = merge(local.common_tags, { Name = "${local.full_name}-${each.key}-failed-invocations" })
}
