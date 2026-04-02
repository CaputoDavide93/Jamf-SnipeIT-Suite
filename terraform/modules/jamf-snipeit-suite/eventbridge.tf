# =============================================================================
# EventBridge — Scheduled ECS Task Trigger
# =============================================================================

resource "aws_cloudwatch_event_rule" "scheduled_run" {
  name                = "${local.full_name}-scheduled-run"
  description         = "Trigger Jamf-SnipeIT Suite daily run"
  schedule_expression = var.schedule_expression

  tags = merge(local.common_tags, { Name = "${local.full_name}-schedule" })
}

resource "aws_cloudwatch_event_target" "ecs_task" {
  rule     = aws_cloudwatch_event_rule.scheduled_run.name
  arn      = aws_ecs_cluster.main.arn
  role_arn = aws_iam_role.eventbridge_ecs.arn

  ecs_target {
    task_definition_arn = aws_ecs_task_definition.app.arn
    task_count          = 1
    launch_type         = "FARGATE"
    platform_version    = "LATEST"

    network_configuration {
      subnets          = local.subnet_ids
      security_groups  = [aws_security_group.ecs_task.id]
      assign_public_ip = true
    }
  }
}
