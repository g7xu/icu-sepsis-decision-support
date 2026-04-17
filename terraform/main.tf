terraform {
  required_version = ">= 1.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }
}

provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile
}

# Get current AWS account ID (used for ECR URL)
data "aws_caller_identity" "current" {}

# ═══════════════════════════════════════════════════════════════
# ECR Repository
# ═══════════════════════════════════════════════════════════════

resource "aws_ecr_repository" "app" {
  name                 = var.project_name
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name        = "${var.project_name}-ecr"
    Environment = var.environment
    Project     = var.project_name
  }
}

# Keep only the last 2 untagged images to save storage costs
resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep only last 5 untagged images"
        selection = {
          tagStatus   = "untagged"
          countType   = "imageCountMoreThan"
          countNumber = 2
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

# Get default VPC (or use data source for existing VPC)
data "aws_vpc" "default" {
  default = true
}

# Get default subnets
data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# DB subnet group (required for RDS)
resource "aws_db_subnet_group" "mimiciv" {
  name       = "${var.project_name}-db-subnet-group"
  subnet_ids = data.aws_subnets.default.ids

  tags = {
    Name        = "${var.project_name}-db-subnet-group"
    Environment = var.environment
    Project     = var.project_name
  }
}

# Security group for RDS
resource "aws_security_group" "rds" {
  name        = "${var.project_name}-rds-sg"
  description = "Security group for MIMIC-IV RDS PostgreSQL"
  vpc_id      = data.aws_vpc.default.id

  tags = {
    Name        = "${var.project_name}-rds-sg"
    Environment = var.environment
    Project     = var.project_name
  }
}

resource "aws_security_group_rule" "rds_from_cidr" {
  for_each = toset(var.allowed_cidr_blocks)

  type              = "ingress"
  description       = "PostgreSQL from allowed CIDR blocks (local dev)"
  from_port         = 5432
  to_port           = 5432
  protocol          = "tcp"
  cidr_blocks       = [each.value]
  security_group_id = aws_security_group.rds.id
}

resource "aws_security_group_rule" "rds_from_ec2" {
  type                     = "ingress"
  description              = "PostgreSQL from EC2 web server"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.ec2.id
  security_group_id        = aws_security_group.rds.id
}

resource "aws_security_group_rule" "rds_egress" {
  type              = "egress"
  description       = "Allow all outbound"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.rds.id
}

# RDS PostgreSQL instance
resource "aws_db_instance" "mimiciv" {
  identifier     = "${var.project_name}-db"
  engine         = "postgres"
  engine_version = var.db_engine_version
  instance_class    = var.db_instance_class
  apply_immediately = true

  # Database configuration
  db_name  = var.db_name
  username = var.db_username
  password = var.db_password

  # Storage configuration
  allocated_storage     = var.db_allocated_storage
  max_allocated_storage = var.max_allocated_storage
  storage_type          = var.db_storage_type
  storage_encrypted     = true # Enable encryption at rest

  # Network configuration
  db_subnet_group_name   = aws_db_subnet_group.mimiciv.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  publicly_accessible    = var.publicly_accessible
  port                   = 5432

  # Backup configuration
  backup_retention_period = var.backup_retention_period
  backup_window           = "03:00-04:00"         # UTC
  maintenance_window      = "mon:04:00-mon:05:00" # UTC

  # Snapshot configuration
  skip_final_snapshot       = var.skip_final_snapshot
  final_snapshot_identifier = var.skip_final_snapshot ? null : "${var.project_name}-final-snapshot-${formatdate("YYYY-MM-DD-hhmm", timestamp())}"

  # Performance insights (optional, costs extra)
  performance_insights_enabled = false

  # Monitoring
  monitoring_interval = 0 # Enhanced monitoring disabled (0 = off, 60 = on)

  # Tags
  tags = {
    Name        = "${var.project_name}-db"
    Environment = var.environment
    Project     = var.project_name
    ManagedBy   = "Terraform"
  }
}

# ═══════════════════════════════════════════════════════════════
# EC2 Web Server
# ═══════════════════════════════════════════════════════════════

# ── AMI Data Source ────────────────────────────────────────────

data "aws_ami" "amazon_linux_2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

locals {
  ec2_ami_id = var.ec2_ami_id != "" ? var.ec2_ami_id : data.aws_ami.amazon_linux_2023.id
}

# ── EC2 Security Group ────────────────────────────────────────

resource "aws_security_group" "ec2" {
  name        = "${var.project_name}-ec2-sg"
  description = "Security group for EC2 web server"
  vpc_id      = data.aws_vpc.default.id

  tags = {
    Name        = "${var.project_name}-ec2-sg"
    Environment = var.environment
    Project     = var.project_name
  }
}

resource "aws_security_group_rule" "ec2_ssh" {
  for_each = toset(var.ssh_allowed_cidr_blocks)

  type              = "ingress"
  description       = "SSH access"
  from_port         = 22
  to_port           = 22
  protocol          = "tcp"
  cidr_blocks       = [each.value]
  security_group_id = aws_security_group.ec2.id
}

resource "aws_security_group_rule" "ec2_http" {
  type              = "ingress"
  description       = "HTTP"
  from_port         = 80
  to_port           = 80
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.ec2.id
}

resource "aws_security_group_rule" "ec2_https" {
  type              = "ingress"
  description       = "HTTPS"
  from_port         = 443
  to_port           = 443
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.ec2.id
}

resource "aws_security_group_rule" "ec2_egress" {
  type              = "egress"
  description       = "Allow all outbound"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.ec2.id
}

# ── SSH Key Pair (auto-generated) ─────────────────────────────

resource "aws_key_pair" "ec2" {
  key_name   = "${var.project_name}-key"
  public_key = var.ec2_public_key

  tags = {
    Name        = "${var.project_name}-key"
    Environment = var.environment
    Project     = var.project_name
  }
}

# ── IAM Role + Instance Profile ───────────────────────────────

resource "aws_iam_role" "ec2" {
  name = "${var.project_name}-ec2-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    Name        = "${var.project_name}-ec2-role"
    Environment = var.environment
    Project     = var.project_name
  }
}

resource "aws_iam_role_policy" "ec2_ecr" {
  name = "${var.project_name}-ec2-ecr-policy"
  role = aws_iam_role.ec2.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchCheckLayerAvailability"
        ]
        Resource = aws_ecr_repository.app.arn
      }
    ]
  })
}

resource "aws_iam_role_policy" "ec2_s3" {
  count = var.model_s3_bucket != "" ? 1 : 0
  name  = "${var.project_name}-ec2-s3-policy"
  role  = aws_iam_role.ec2.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket"
        ]
        Resource = [
          "arn:aws:s3:::${var.model_s3_bucket}",
          "arn:aws:s3:::${var.model_s3_bucket}/*"
        ]
      }
    ]
  })
}

resource "aws_iam_instance_profile" "ec2" {
  name = "${var.project_name}-ec2-profile"
  role = aws_iam_role.ec2.name

  tags = {
    Name        = "${var.project_name}-ec2-profile"
    Environment = var.environment
    Project     = var.project_name
  }
}

# ── EC2 Instance ──────────────────────────────────────────────

resource "aws_instance" "web" {
  ami                    = local.ec2_ami_id
  instance_type          = var.ec2_instance_type
  key_name               = aws_key_pair.ec2.key_name
  vpc_security_group_ids = [aws_security_group.ec2.id]
  iam_instance_profile   = aws_iam_instance_profile.ec2.name

  root_block_device {
    volume_size           = 20
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }

  user_data = templatefile("${path.module}/user_data.sh.tpl", {
    aws_region        = var.aws_region
    ecr_url           = aws_ecr_repository.app.repository_url
    db_name           = var.db_name
    db_username       = var.db_username
    db_password       = var.db_password
    db_host           = aws_db_instance.mimiciv.address
    db_port           = aws_db_instance.mimiciv.port
    django_secret_key = var.django_secret_key
    domain_name       = var.domain_name
    model_service_url = var.model_service_url
    model_s3_bucket   = var.model_s3_bucket
    cf_origin_cert    = var.cf_origin_cert
    cf_origin_key     = var.cf_origin_key
  })

  tags = {
    Name        = "${var.project_name}-web"
    Environment = var.environment
    Project     = var.project_name
  }

}

# ── Elastic IP ────────────────────────────────────────────────

resource "aws_eip" "web" {
  domain = "vpc"

  tags = {
    Name        = "${var.project_name}-web-eip"
    Environment = var.environment
    Project     = var.project_name
  }
}

resource "aws_eip_association" "web" {
  instance_id   = aws_instance.web.id
  allocation_id = aws_eip.web.id
}
