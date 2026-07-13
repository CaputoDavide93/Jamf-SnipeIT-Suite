# =============================================================================
# EventBridge — Scheduled ECS Task Triggers
# =============================================================================
# Four cron rules drive the suite. Each rule fires the same Fargate task
# definition but overrides the container `command` and `RUN_MODE` env so the
# entrypoint dispatches to a specific CLI subcommand or run-group.

locals {
  schedules = {
    sync = {
      cron        = var.schedule_expression
      description = "Full sync (correction → user_match → snipe_to_jamf → leavers)"
      run_mode    = "run-once"
      command     = []
    }
    starters = {
      cron        = "cron(50 5 ? * MON *)" # 05:50 UTC staggered starters
      description = "Mon 05:50 UTC — Azure starters chain"
      run_mode    = "cli"
      command     = ["run-group", "--modules", "azure-starters,user-enrichment,peripherals-sync"]
    }
    housekeeping = {
      cron        = "cron(0 21 ? * SUN *)"
      description = "Sun 21:00 UTC — housekeeping (cleanup, pending-reconciliation, username-standardize, ai-audit, reconciliation)"
      run_mode    = "cli"
      command     = ["run-group", "--modules", "cleanup,pending-reconciliation,username-standardize,ai-audit,reconciliation"]
    }
    health = {
      cron        = "cron(0 19 ? * MON,THU *)"
      description = "Mon+Thu 19:00 UTC — health check"
      run_mode    = "cli"
      command     = ["run-group", "--modules", "health-check"]
    }
    monthly-digest = {
      cron        = "cron(0 9 ? * 2#1 *)"
      description = "First Mon of month 09:00 UTC — monthly digest Slack report"
      run_mode    = "cli"
      command     = ["run-group", "--modules", "monthly-digest"]
    }
  }
}

resource "aws_cloudwatch_event_rule" "scheduled" {
  for_each            = local.schedules
  name                = "${local.full_name}-${each.key}"
  description         = each.value.description
  schedule_expression = each.value.cron

  tags = merge(local.common_tags, { Name = "${local.full_name}-${each.key}" })
}

resource "aws_cloudwatch_event_target" "ecs_task" {
  for_each = local.schedules

  rule     = aws_cloudwatch_event_rule.scheduled[each.key].name
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

  input = jsonencode({
    containerOverrides = [
      merge(
        {
          name = "app"
          environment = [
            { name = "RUN_MODE", value = each.value.run_mode },
          ]
        },
        length(each.value.command) > 0 ? { command = each.value.command } : {}
      )
    ]
  })
}
