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
  value = <<-EOT
    DB_NAME=${aws_db_instance.mimiciv.db_name}
    DB_USER=${aws_db_instance.mimiciv.username}
    DB_PASSWORD=<REDACTED>
    DB_HOST=${aws_db_instance.mimiciv.address}
    DB_PORT=${aws_db_instance.mimiciv.port}
    DB_SCHEMA=mimiciv_derived
  EOT
  sensitive = true
}
