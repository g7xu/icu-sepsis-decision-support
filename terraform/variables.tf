variable "aws_profile" {
  description = "AWS CLI profile to use for authentication (e.g. SSO profile name from ~/.aws/config)"
  type        = string
  default     = "default"
}

variable "aws_region" {
  description = "AWS region for resources"
  type        = string
  default     = "us-east-1"

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-[0-9]$", var.aws_region))
    error_message = "aws_region must be a valid AWS region (e.g. us-east-1, eu-west-2)."
  }
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
  description = "Master password for RDS. Set via: export TF_VAR_db_password=..."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.db_password) >= 12
    error_message = "db_password must be at least 12 characters long."
  }

  validation {
    condition     = !contains(["CHANGE_ME_TO_STRONG_PASSWORD", "password", "postgres"], var.db_password)
    error_message = "db_password must not be a placeholder or common password."
  }
}

variable "db_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t4g.micro" # Free tier eligible
}

variable "db_allocated_storage" {
  description = "Initial allocated storage in GB"
  type        = number
  default     = 20 # Start at Free Tier limit

  validation {
    condition     = var.db_allocated_storage >= 20
    error_message = "db_allocated_storage must be at least 20 GB (RDS minimum)."
  }
}

variable "max_allocated_storage" {
  description = "Maximum storage for auto-scaling in GB"
  type        = number
  default     = 30 # Auto-scale up to 30 GB (sufficient for app tables subset)

  validation {
    condition     = var.max_allocated_storage >= 20
    error_message = "max_allocated_storage must be at least 20 GB (RDS minimum)."
  }
}

variable "db_storage_type" {
  description = "Storage type (gp2, gp3, io1, io2)"
  type        = string
  default     = "gp2"

  validation {
    condition     = contains(["gp2", "gp3", "io1", "io2"], var.db_storage_type)
    error_message = "db_storage_type must be one of: gp2, gp3, io1, io2."
  }
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

  validation {
    condition     = var.backup_retention_period >= 0 && var.backup_retention_period <= 35
    error_message = "backup_retention_period must be between 0 and 35 days."
  }
}

variable "skip_final_snapshot" {
  description = "Skip final snapshot when destroying (set to false for production)"
  type        = bool
  default     = true
}

variable "publicly_accessible" {
  description = "Whether RDS should be publicly accessible"
  type        = bool
  default     = true # Set to false if only accessing from within VPC
}

variable "allowed_cidr_blocks" {
  description = "CIDR blocks allowed to access RDS (for security group). Override in terraform.tfvars with your IP."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of: dev, staging, prod."
  }
}

variable "project_name" {
  description = "Project name for resource naming"
  type        = string
  default     = "icu-sepsis"
}

# ── EC2 Variables ──────────────────────────────────────────────

variable "ec2_instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t3.small"

  validation {
    condition     = can(regex("^[a-z][0-9][a-z]?\\.(nano|micro|small|medium|large|xlarge|[0-9]+xlarge)$", var.ec2_instance_type))
    error_message = "ec2_instance_type must be a valid EC2 instance type (e.g. t3.small, t3.medium)."
  }
}

variable "ec2_ami_id" {
  description = "EC2 AMI ID (leave empty to auto-lookup latest Amazon Linux 2023)"
  type        = string
  default     = ""
}

variable "ec2_public_key" {
  description = "SSH public key for EC2 key pair. Generate with: ssh-keygen -t rsa -b 4096 -f terraform/icu-sepsis-team-key"
  type        = string
  default     = ""
}

variable "ssh_allowed_cidr_blocks" {
  description = "CIDR blocks allowed to SSH into EC2. Override in terraform.tfvars with your IP."
  type        = list(string)
  default     = ["0.0.0.0/0"]

  validation {
    condition     = length(var.ssh_allowed_cidr_blocks) > 0
    error_message = "ssh_allowed_cidr_blocks must contain at least one CIDR block."
  }
}

# ── App / Django Variables ─────────────────────────────────────

variable "django_secret_key" {
  description = "Django SECRET_KEY. Set via: export TF_VAR_django_secret_key=..."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.django_secret_key) >= 40
    error_message = "django_secret_key must be at least 40 characters long."
  }

  validation {
    condition     = !contains(["CHANGE_ME_TO_RANDOM_SECRET_KEY", "secret", "django-insecure"], var.django_secret_key)
    error_message = "django_secret_key must not be a placeholder value."
  }

  validation {
    condition     = !can(regex("[$]", var.django_secret_key))
    error_message = "django_secret_key must not contain '$' — it breaks shell expansion in EC2 user_data."
  }
}

variable "domain_name" {
  description = "Domain name for the app (e.g. icu-sepsis-detect.g7xu.dev). Used for nginx server_name and ALLOWED_HOSTS."
  type        = string
  default     = ""
}

variable "model_service_url" {
  description = "URL of the external ML model service (empty = stub mode)"
  type        = string
  default     = ""
}

# ── Cloudflare Origin Certificate ─────────────────────────────

variable "cf_origin_cert" {
  description = "Cloudflare Origin Certificate PEM (from Cloudflare dashboard). Set in secrets.auto.tfvars."
  type        = string
  sensitive   = true
  default     = ""
}

variable "cf_origin_key" {
  description = "Cloudflare Origin Certificate private key PEM. Set in secrets.auto.tfvars."
  type        = string
  sensitive   = true
  default     = ""
}
