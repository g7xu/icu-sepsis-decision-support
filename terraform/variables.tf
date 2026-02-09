variable "aws_region" {
  description = "AWS region for resources"
  type        = string
  default     = "us-east-1"
}

variable "db_name" {
  description = "Name of the database"
  type        = string
  default     = "mimiciv"
}

variable "db_username" {
  description = "Master username for RDS"
  type        = string
  default     = "postgres"
  sensitive   = true
}

variable "db_password" {
  description = "Master password for RDS (should be provided via tfvars or env var)"
  type        = string
  sensitive   = true
}

variable "db_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t4g.micro"  # Free tier eligible
}

variable "db_allocated_storage" {
  description = "Initial allocated storage in GB"
  type        = number
  default     = 20  # Start at Free Tier limit
}

variable "max_allocated_storage" {
  description = "Maximum storage for auto-scaling in GB"
  type        = number
  default     = 130  # Auto-scale up to 130 GB for full MIMIC-IV dataset
}

variable "db_storage_type" {
  description = "Storage type (gp2, gp3, io1, io2)"
  type        = string
  default     = "gp2"
}

variable "db_engine_version" {
  description = "PostgreSQL engine version"
  type        = string
  default     = "15.14"
}

variable "backup_retention_period" {
  description = "Number of days to retain backups"
  type        = number
  default     = 7
}

variable "skip_final_snapshot" {
  description = "Skip final snapshot when destroying (set to false for production)"
  type        = bool
  default     = true
}

variable "publicly_accessible" {
  description = "Whether RDS should be publicly accessible"
  type        = bool
  default     = true  # Set to false if only accessing from within VPC
}

variable "allowed_cidr_blocks" {
  description = "CIDR blocks allowed to access RDS (for security group)"
  type        = list(string)
  default     = ["0.0.0.0/0"]  # WARNING: Restrict this in production!
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "project_name" {
  description = "Project name for resource naming"
  type        = string
  default     = "icu-sepsis"
}
