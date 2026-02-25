#!/bin/bash
set -euo pipefail
exec > /var/log/user-data.log 2>&1
echo "=== User data script started at $(date) ==="

# ── 1. Install packages ──────────────────────────────────────

dnf update -y
dnf install -y docker nginx

# Start and enable Docker
systemctl start docker
systemctl enable docker
usermod -aG docker ec2-user

echo "Docker installed"
docker --version

# ── 2. Write .env file ───────────────────────────────────────
# Done before any docker steps so the file always exists on disk,
# even if the first docker pull fails (no image in ECR at boot time).
#
# NOTE: PUBLIC_IP is fetched here (at EC2 boot time) and stored in a shell
# variable BEFORE the heredoc. This prevents Terraform from trying to evaluate
# $(curl ...) at render time (when the instance doesn't exist yet).

APP_DIR="/opt/icu-sepsis"
mkdir -p "$APP_DIR"

PUBLIC_IP=$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4 || echo localhost)

cat > "$APP_DIR/.env" <<ENVEOF
DB_NAME=${db_name}
DB_USER=${db_username}
DB_PASSWORD=${db_password}
DB_HOST=${db_host}
DB_PORT=${db_port}
DB_SCHEMA=mimiciv_derived
SECRET_KEY=${django_secret_key}
DEBUG=False
ALLOWED_HOSTS=${domain_name},$PUBLIC_IP,localhost
DEMO_MODE=true
ENVEOF
chmod 600 "$APP_DIR/.env"

echo ".env file written"

# ── 3. Authenticate to ECR and pull image ─────────────────────
# These steps are non-fatal: on first boot no image may exist yet.
# deploy.sh handles the actual container start on first deploy.

aws ecr get-login-password --region "${aws_region}" \
  | docker login --username AWS --password-stdin "${ecr_url}" || true

docker pull "${ecr_url}:latest" || {
  echo "Image not yet in ECR — skipping container start. Run ./deploy.sh to deploy."
  # Still configure nginx below so the instance is ready when the image arrives.
}

# ── 4. Start container if image was pulled ────────────────────

if docker image inspect "${ecr_url}:latest" &>/dev/null; then
  docker run -d \
    --name icu-sepsis-web \
    --restart unless-stopped \
    --env-file "$APP_DIR/.env" \
    -p 127.0.0.1:8000:8000 \
    "${ecr_url}:latest" || true

  echo "Container started"

  sleep 10
  docker exec icu-sepsis-web python manage.py migrate --noinput || true
  echo "Migrations complete"
else
  echo "Skipping container start — image not available."
fi

# ── 5. Configure nginx ────────────────────────────────────────

%{ if domain_name != "" ~}
SERVER_NAME="${domain_name}"
%{ else ~}
SERVER_NAME="_"
%{ endif ~}

cat > /etc/nginx/conf.d/icu-sepsis.conf <<NGINXEOF
server {
    listen 80;
    server_name $SERVER_NAME;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
    }
}
NGINXEOF

rm -f /etc/nginx/conf.d/default.conf

nginx -t
systemctl start nginx
systemctl enable nginx

echo "Nginx configured and started"

echo "=== User data script completed at $(date) ==="
echo "App should be accessible on port 80 after ./deploy.sh is run"
