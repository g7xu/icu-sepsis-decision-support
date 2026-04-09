output "db_endpoint" {
  description = "RDS instance endpoint"
  value       = aws_db_instance.mimiciv.endpoint
}

output "db_address" {
  description = "RDS instance address (hostname without port)"
  value       = aws_db_instance.mimiciv.address
}

output "db_port" {
  description = "RDS instance port"
  value       = aws_db_instance.mimiciv.port
}

output "db_name" {
  description = "Database name"
  value       = aws_db_instance.mimiciv.db_name
}

output "db_username" {
  description = "Master username"
  value       = aws_db_instance.mimiciv.username
  sensitive   = true
}

output "security_group_id" {
  description = "Security group ID for RDS"
  value       = aws_security_group.rds.id
}

output "connection_string" {
  description = "PostgreSQL connection string (without password)"
  value       = "postgresql://${aws_db_instance.mimiciv.username}@${aws_db_instance.mimiciv.endpoint}/${aws_db_instance.mimiciv.db_name}"
  sensitive   = true
}

# Output for .env file format
output "env_file_content" {
  description = "Environment variables for .env file"
  value       = <<-EOT
    DB_NAME=${aws_db_instance.mimiciv.db_name}
    DB_USER=${aws_db_instance.mimiciv.username}
    DB_PASSWORD=<REDACTED>
    DB_HOST=${aws_db_instance.mimiciv.address}
    DB_PORT=${aws_db_instance.mimiciv.port}
    DB_SCHEMA=mimiciv_derived
  EOT
  sensitive   = true
}

# ── ECR Outputs ───────────────────────────────────────────────

output "ecr_repository_url" {
  description = "ECR repository URL for docker push/pull"
  value       = aws_ecr_repository.app.repository_url
}

output "aws_region" {
  description = "AWS region"
  value       = var.aws_region
}

output "project_name" {
  description = "Project name (used by deploy.sh for key file path)"
  value       = var.project_name
}

# ── EC2 Outputs ───────────────────────────────────────────────

output "ec2_public_ip" {
  description = "Elastic IP address of the EC2 web server"
  value       = aws_eip.web.public_ip
}

output "ec2_instance_id" {
  description = "EC2 instance ID"
  value       = aws_instance.web.id
}

output "app_url" {
  description = "Application URL"
  value       = var.domain_name != "" ? "https://${var.domain_name}" : "http://${aws_eip.web.public_ip}"
}

output "ssh_command" {
  description = "SSH command to connect to the EC2 instance"
  value       = "ssh -i terraform/${var.project_name}-key.pem ec2-user@${aws_eip.web.public_ip}"
}

