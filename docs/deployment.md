# Deployment to AWS

The application can be deployed to AWS using Terraform (for infrastructure) and a provided deploy script.

## Prerequisites

- Database already set up (see [Database Setup](database-setup.md))
- AWS CLI configured (`aws configure`)
- Terraform >= 1.0
- `.env` file configured with database credentials

## Infrastructure

If you haven't already provisioned AWS resources, follow the Terraform steps in [Database Setup — Option B: AWS RDS](database-setup.md#option-b-aws-rds).

The Terraform configuration creates: RDS (PostgreSQL), EC2 (web server), ECR (container registry), VPC security groups, and an Elastic IP.

### Instance Recommendations

| Resource | Recommended | Notes |
|----------|-------------|-------|
| RDS | `db.t4g.micro` | Sufficient for cached queries (~60 patients). Free tier eligible. |
| EC2 | `t3.small` | Runs Django + gunicorn + Nginx. |

### Additional Terraform Variables for Deployment

Edit `terraform/terraform.tfvars`:
```hcl
django_secret_key       = "your-random-key"           # Generate: python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
ssh_allowed_cidr_blocks = ["<your-ip>/32"]             # Restrict SSH access
domain_name             = "your-domain.example.com"
```

Then apply:
```bash
cd terraform
terraform apply
```

## Deploy

```bash
# Source environment for deploy script
set -a && source .env && set +a

# Full deploy: build Docker image → push to ECR → deploy to EC2
./deploy.sh

# Or build and push only (no SSH deploy)
./deploy.sh --build-only
```

The script builds a `linux/amd64` image, pushes to ECR, SSHs into EC2, pulls the image, runs migrations, and configures Nginx with SSL.
