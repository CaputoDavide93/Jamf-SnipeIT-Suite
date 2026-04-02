# =============================================================================
# Outputs
# =============================================================================

output "ecr_repository_url" {
  description = "ECR repository URL for pushing Docker images"
  value       = aws_ecr_repository.app.repository_url
}

output "ecr_repository_name" {
  description = "ECR repository name"
  value       = aws_ecr_repository.app.name
}

output "ecs_cluster_name" {
  description = "ECS cluster name"
  value       = aws_ecs_cluster.main.name
}

output "ecs_cluster_arn" {
  description = "ECS cluster ARN"
  value       = aws_ecs_cluster.main.arn
}

output "task_definition_arn" {
  description = "ECS task definition ARN"
  value       = aws_ecs_task_definition.app.arn
}

output "log_group_name" {
  description = "CloudWatch log group name"
  value       = aws_cloudwatch_log_group.ecs.name
}

output "security_group_id" {
  description = "Security group ID for ECS tasks"
  value       = aws_security_group.ecs_task.id
}

output "schedule_rule_name" {
  description = "EventBridge schedule rule name"
  value       = aws_cloudwatch_event_rule.scheduled_run.name
}

output "docker_push_commands" {
  description = "Commands to build and push the Docker image"
  value       = <<-EOT
    # Authenticate Docker to ECR
    aws ecr get-login-password --region ${var.aws_region} | docker login --username AWS --password-stdin ${aws_ecr_repository.app.repository_url}

    # Build and push
    docker build -t ${aws_ecr_repository.app.repository_url}:latest .
    docker push ${aws_ecr_repository.app.repository_url}:latest
  EOT
}
