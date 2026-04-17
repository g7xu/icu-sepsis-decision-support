#!/usr/bin/env bash
set -euo pipefail

# ── Deploy ICU Sepsis app to AWS (ECR + EC2) ─────────────────
# Usage:
#   ./deploy.sh              # Build, push to ECR, deploy to EC2
#   ./deploy.sh --build-only # Build and push only (no SSH deploy)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

BUILD_ONLY=false
if [[ "${1:-}" == "--build-only" ]]; then
  BUILD_ONLY=true
fi

# ── 1. Read Terraform outputs ────────────────────────────────

echo "==> Reading Terraform outputs..."
cd "$SCRIPT_DIR"

ECR_URL=$(terraform output -raw ecr_repository_url)
EC2_IP=$(terraform output -raw ec2_public_ip)
AWS_REGION=$(terraform output -raw aws_region)
PROJECT_NAME=$(terraform output -raw project_name)
DOMAIN_NAME=$(terraform output -raw app_url | sed 's|https://||;s|http://||')
KEY_FILE="${SCRIPT_DIR}/${PROJECT_NAME}-key"

echo "    ECR:  $ECR_URL"
echo "    EC2:  $EC2_IP"
echo "    Region: $AWS_REGION"

# ── 2. Build Docker image ────────────────────────────────────

echo "==> Building Docker image..."
cd "$PROJECT_ROOT"
docker build --platform linux/amd64 -t "${PROJECT_NAME}:latest" .

# ── 3. Authenticate to ECR ───────────────────────────────────

echo "==> Authenticating to ECR..."
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$ECR_URL"

# ── 4. Tag and push ──────────────────────────────────────────

echo "==> Pushing image to ECR..."
docker tag "${PROJECT_NAME}:latest" "${ECR_URL}:latest"
docker push "${ECR_URL}:latest"

echo "==> Image pushed successfully: ${ECR_URL}:latest"

if $BUILD_ONLY; then
  echo "==> --build-only: skipping EC2 deployment."
  exit 0
fi

# ── 5. Deploy on EC2 via SSH ─────────────────────────────────

echo "==> Deploying on EC2 ($EC2_IP)..."

if [[ ! -f "$KEY_FILE" ]]; then
  echo "ERROR: SSH key not found at $KEY_FILE"
  echo "Run 'terraform apply' first to generate the key."
  exit 1
fi

SSH_CMD=(ssh -i "$KEY_FILE" -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$SCRIPT_DIR/.deploy_known_hosts" -o ConnectTimeout=10 "ec2-user@$EC2_IP")

echo "==> Waiting for EC2 to finish booting..."
for i in $(seq 1 30); do
  "${SSH_CMD[@]}" "docker --version" 2>/dev/null && break
  echo "    Not ready yet... ($i/30)"
  sleep 10
done

"${SSH_CMD[@]}" << REMOTE
set -euo pipefail

echo "--- Updating ALLOWED_HOSTS in .env ---"
sudo sed -i "s|^ALLOWED_HOSTS=.*|ALLOWED_HOSTS=$DOMAIN_NAME,$EC2_IP,localhost|" /opt/icu-sepsis/.env
sudo grep ALLOWED_HOSTS /opt/icu-sepsis/.env

echo "--- Authenticating to ECR ---"
aws ecr get-login-password --region "$AWS_REGION" \
  | sudo docker login --username AWS --password-stdin "$ECR_URL"

echo "--- Pulling latest image ---"
sudo docker pull "${ECR_URL}:latest"

echo "--- Stopping old container ---"
sudo docker stop icu-sepsis-web 2>/dev/null || true
sudo docker rm icu-sepsis-web 2>/dev/null || true

echo "--- Starting new container ---"
sudo docker run -d \
  --name icu-sepsis-web \
  --restart unless-stopped \
  --env-file /opt/icu-sepsis/.env \
  -p 127.0.0.1:8000:8000 \
  "${ECR_URL}:latest"

echo "--- Running migrations ---"
sleep 5
sudo docker exec icu-sepsis-web python manage.py migrate --noinput

echo "--- Collecting static files ---"
sudo docker exec icu-sepsis-web python manage.py collectstatic --noinput 2>/dev/null || true

echo "--- Done ---"
sudo docker ps --filter name=icu-sepsis-web
REMOTE

echo ""
echo "==> Deployment complete!"
echo "    Visit: http://${EC2_IP}/patients/"
