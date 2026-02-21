#!/usr/bin/env bash
# Run SQL view setup scripts using connection details from .env
# Usage: ./run_setup_views.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ ! -f .env ]]; then
  echo "Error: .env not found. Copy .env.example to .env and fill in DB_HOST, DB_USER, DB_NAME, DB_PASSWORD."
  exit 1
fi

# Load .env (DB_HOST, DB_USER, DB_NAME, DB_PASSWORD, DB_PORT)
set -a
source .env
set +a

DB_HOST="${DB_HOST:?Missing DB_HOST in .env}"
DB_USER="${DB_USER:-postgres}"
DB_NAME="${DB_NAME:-mimiciv}"
DB_PORT="${DB_PORT:-5432}"

if [[ -z "${DB_PASSWORD:-}" ]]; then
  echo "Warning: DB_PASSWORD not set in .env. You will be prompted for password for each script."
  echo "To avoid prompts, add DB_PASSWORD=your_password to .env"
  echo ""
fi

export PGPASSWORD="${DB_PASSWORD:-}"

echo "Running view setup scripts against $DB_HOST:$DB_PORT/$DB_NAME ..."
echo ""

for f in scripts/01_first_icu_stay.sql \
         scripts/02_fis_icd9.sql \
         scripts/03_fis_icd9_titled.sql \
         scripts/04_fisi9t_profile.sql \
         scripts/05_fisi9t_unique_patient_profile.sql \
         scripts/06_fisi9t_vitalsign_hourly.sql \
         scripts/07_fisi9t_procedureevents_hourly.sql \
         scripts/08_fisi9t_chemistry_hourly.sql \
         scripts/09_fisi9t_coagulation_hourly.sql; do
  if [[ ! -f "$f" ]]; then
    echo "Error: $f not found"
    exit 1
  fi
  echo "[$(date +%H:%M:%S)] Running $f ..."
  psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -f "$f"
done

# Clear PGPASSWORD from environment
unset PGPASSWORD

echo ""
echo "Done. All view scripts completed successfully."
