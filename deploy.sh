#!/usr/bin/env bash
#
# Build the Docker image, push to ECR, and deploy to EC2.
#
# Usage:
#   ./deploy.sh              # Build, push, and deploy
#   ./deploy.sh --build-only # Build and push to ECR only (no SSH deploy)
#
# Prerequisites:
#   - AWS CLI configured with ECR push permissions
#   - Terraform already applied (ECR repo + EC2 exist)
#   - Docker running locally
#   - TF_VAR_db_password and TF_VAR_django_secret_key set in .env
#   - Cloudflare Origin Certificate saved as:
#       ssl/cloudflare-origin.pem  (certificate)
#       ssl/cloudflare-origin.key  (private key)
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TERRAFORM_DIR="$SCRIPT_DIR/terraform"

# ── Load .env (exports TF_VAR_* and other secrets) ────────────
ENV_FILE="$SCRIPT_DIR/.env"
if [[ -f "$ENV_FILE" ]]; then
  set -a && source "$ENV_FILE" && set +a
  echo "Loaded $ENV_FILE"
else
  echo "WARNING: $ENV_FILE not found. Falling back to exported env vars."
fi

# ── Guard: require secrets in environment ─────────────────────

if [[ -z "${TF_VAR_db_password:-}" ]]; then
  echo "ERROR: TF_VAR_db_password is not set."
  echo "  export TF_VAR_db_password=\"your-db-password\""
  exit 1
fi

if [[ -z "${TF_VAR_django_secret_key:-}" ]]; then
  echo "ERROR: TF_VAR_django_secret_key is not set."
  echo "  export TF_VAR_django_secret_key=\"\$(python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())')\""
  exit 1
fi

SSL_CERT="$SCRIPT_DIR/ssl/cloudflare-origin.pem"
SSL_KEY="$SCRIPT_DIR/ssl/cloudflare-origin.key"

if [[ ! -f "$SSL_CERT" || ! -f "$SSL_KEY" ]]; then
  echo "ERROR: Cloudflare Origin Certificate not found."
  echo "  Generate one at: Cloudflare → SSL/TLS → Origin Server → Create Certificate"
  echo "  Save as: ssl/cloudflare-origin.pem and ssl/cloudflare-origin.key"
  exit 1
fi

# ── Read Terraform outputs ────────────────────────────────────

echo "Reading Terraform outputs..."
ECR_URL=$(terraform -chdir="$TERRAFORM_DIR" output -raw ecr_repository_url)
AWS_REGION=$(terraform -chdir="$TERRAFORM_DIR" output -raw aws_region 2>/dev/null || echo "us-east-1")
EC2_IP=$(terraform -chdir="$TERRAFORM_DIR" output -raw ec2_public_ip)
PROJECT_NAME=$(terraform -chdir="$TERRAFORM_DIR" output -raw project_name 2>/dev/null || echo "icu-sepsis")
KEY_FILE="$TERRAFORM_DIR/${PROJECT_NAME}-key.pem"

DB_HOST=$(terraform -chdir="$TERRAFORM_DIR" output -raw db_address)
DB_PORT=$(terraform -chdir="$TERRAFORM_DIR" output -raw db_port)
DB_NAME=$(terraform -chdir="$TERRAFORM_DIR" output -raw db_name)
DOMAIN_NAME=$(terraform -chdir="$TERRAFORM_DIR" output -raw app_url 2>/dev/null | sed 's|https://||;s|http://||' || echo "$EC2_IP")

# Read from .env (with safe fallbacks)
DB_USER="${DB_USER:-postgres}"
DJANGO_DEBUG="${DEBUG:-False}"
DJANGO_DEMO_MODE="${DEMO_MODE:-true}"

echo "  ECR:     $ECR_URL"
echo "  EC2:     $EC2_IP"
echo "  DB host: $DB_HOST"
echo "  Domain:  $DOMAIN_NAME"

# ── Build the Docker image ────────────────────────────────────

echo ""
echo "Building Docker image..."
docker build --platform linux/amd64 -t "$ECR_URL:latest" "$SCRIPT_DIR"

# ── Push to ECR ───────────────────────────────────────────────

echo ""
echo "Authenticating to ECR..."
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$ECR_URL"

echo "Pushing image to ECR..."
docker push "$ECR_URL:latest"

echo "Image pushed: $ECR_URL:latest"

if [[ "${1:-}" == "--build-only" ]]; then
  echo ""
  echo "Done (--build-only). Image is in ECR. SSH into EC2 to pull manually."
  exit 0
fi

# ── Deploy to EC2 ─────────────────────────────────────────────

echo ""
echo "Deploying to EC2 ($EC2_IP)..."

# Cache EC2 host key to avoid StrictHostKeyChecking=no
KNOWN_HOSTS_FILE=$(mktemp)
trap 'rm -f "$KNOWN_HOSTS_FILE"' EXIT
ssh-keyscan -T 10 "$EC2_IP" >> "$KNOWN_HOSTS_FILE" 2>/dev/null

SSH_OPTS="-i $KEY_FILE -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$KNOWN_HOSTS_FILE"

# Copy Cloudflare Origin Certificate to EC2
echo "Copying SSL certificates to EC2..."
scp $SSH_OPTS "$SSL_CERT" ec2-user@"$EC2_IP":/tmp/cloudflare-origin.pem
scp $SSH_OPTS "$SSL_KEY"  ec2-user@"$EC2_IP":/tmp/cloudflare-origin.key
ssh $SSH_OPTS ec2-user@"$EC2_IP" "sudo mkdir -p /etc/nginx/ssl && sudo mv /tmp/cloudflare-origin.pem /etc/nginx/ssl/ && sudo mv /tmp/cloudflare-origin.key /etc/nginx/ssl/ && sudo chmod 600 /etc/nginx/ssl/cloudflare-origin.key && sudo chmod 644 /etc/nginx/ssl/cloudflare-origin.pem"
echo "SSL certificates copied"

ssh $SSH_OPTS ec2-user@"$EC2_IP" bash -s <<REMOTE
set -euo pipefail

# Authenticate to ECR
aws ecr get-login-password --region "$AWS_REGION" \
  | sudo docker login --username AWS --password-stdin "$ECR_URL"

# Pull the new image
sudo docker pull "$ECR_URL:latest"

# Stop the old container
sudo docker stop icu-sepsis-web 2>/dev/null || true
sudo docker rm icu-sepsis-web 2>/dev/null || true

# Write .env file with fresh values
sudo mkdir -p /opt/icu-sepsis
sudo tee /opt/icu-sepsis/.env > /dev/null <<ENVEOF
DB_HOST=$DB_HOST
DB_PORT=$DB_PORT
DB_NAME=$DB_NAME
DB_USER=$DB_USER
DB_PASSWORD=$TF_VAR_db_password
DB_SCHEMA=mimiciv_derived
SECRET_KEY=$TF_VAR_django_secret_key
DEBUG=$DJANGO_DEBUG
ALLOWED_HOSTS=$DOMAIN_NAME,$EC2_IP,localhost
DEMO_MODE=$DJANGO_DEMO_MODE
ENVEOF
sudo chmod 600 /opt/icu-sepsis/.env

echo ".env written"

# Start the new container
sudo docker run -d \
  --name icu-sepsis-web \
  --restart unless-stopped \
  --env-file /opt/icu-sepsis/.env \
  -p 127.0.0.1:8000:8000 \
  "$ECR_URL:latest"

# Run migrations
sleep 5
sudo docker exec icu-sepsis-web python manage.py migrate --noinput

# Ensure Nginx is installed, configured, and running
sudo dnf install -y nginx 2>/dev/null || true
sudo rm -f /etc/nginx/conf.d/default.conf
sudo tee /etc/nginx/conf.d/icu-sepsis.conf > /dev/null <<'NGINXEOF'
# Redirect all HTTP to HTTPS
server {
    listen 80 default_server;
    server_name _;
    return 301 https://\$host\$request_uri;
}

# HTTPS server with Cloudflare Origin Certificate
server {
    listen 443 ssl default_server;
    server_name _;

    ssl_certificate     /etc/nginx/ssl/cloudflare-origin.pem;
    ssl_certificate_key /etc/nginx/ssl/cloudflare-origin.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
    }
}
NGINXEOF
sudo nginx -t && sudo systemctl enable --now nginx && sudo systemctl reload nginx

# Clean up old images
sudo docker image prune -f

echo "Deploy complete!"
REMOTE

echo ""
echo "Deployment successful!"
APP_URL=$(terraform -chdir="$TERRAFORM_DIR" output -raw app_url 2>/dev/null || echo "http://$EC2_IP")
echo "App: $APP_URL"
