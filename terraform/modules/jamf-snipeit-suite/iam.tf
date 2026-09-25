# =============================================================================
# IAM — ECS Task Execution Role + Task Role
# =============================================================================

# -- Execution Role (ECS agent: pull images, write logs, read secrets) --
resource "aws_iam_role" "ecs_execution" {
  name = "${local.full_name}-ecs-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })

  tags = merge(local.common_tags, { Name = "${local.full_name}-ecs-execution" })
}

resource "aws_iam_role_policy_attachment" "ecs_execution_base" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Allow reading secrets from SSM Parameter Store
resource "aws_iam_role_policy" "ecs_execution_ssm" {
  name = "ssm-read"
  role = aws_iam_role.ecs_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ssm:GetParameters", "ssm:GetParameter"]
      Resource = "arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter/${local.full_name}/*"
    }]
  })
}

# -- Task Role (application permissions — currently none needed) --
resource "aws_iam_role" "ecs_task" {
  name = "${local.full_name}-ecs-task"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })

  tags = merge(local.common_tags, { Name = "${local.full_name}-ecs-task" })
}

# Allow the application's RunMutex to manage the distributed run-lock
# parameter (get/put/delete) so scheduled runs serialise correctly.
resource "aws_iam_role_policy" "ecs_task_run_lock" {
  name = "run-lock-mutex"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ssm:GetParameter", "ssm:PutParameter", "ssm:DeleteParameter"]
      Resource = "arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter/${local.full_name}/run-lock"
    }]
  })
}

# -- EventBridge Role (to launch ECS tasks) --
resource "aws_iam_role" "eventbridge_ecs" {
  name = "${local.full_name}-eventbridge-ecs"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
    }]
  })

  tags = merge(local.common_tags, { Name = "${local.full_name}-eventbridge-ecs" })
}

resource "aws_iam_role_policy" "eventbridge_run_task" {
  name = "run-ecs-task"
  role = aws_iam_role.eventbridge_ecs.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = "ecs:RunTask"
        # Wildcard revision, not aws_ecs_task_definition.app.arn (revision-pinned).
        # On 2026-08-03/06 the task-def was bumped to rev 6 via direct AWS CLI
        # calls (not `terraform apply`), so the revision-pinned policy went
        # stale at rev 4 and every scheduled EventBridge trigger failed with
        # AccessDenied for 8 days with zero tasks launched. A wildcard makes
        # the policy immune to task-def revision drift regardless of how the
        # revision was bumped.
        Resource = "${replace(aws_ecs_task_definition.app.arn, "/:\\d+$/", "")}:*"
        Condition = {
          ArnLike = {
            "ecs:cluster" = aws_ecs_cluster.main.arn
          }
        }
      },
      {
        Effect = "Allow"
        Action = ["iam:PassRole"]
        Resource = [
          aws_iam_role.ecs_execution.arn,
          aws_iam_role.ecs_task.arn,
        ]
      }
    ]
  })
}
