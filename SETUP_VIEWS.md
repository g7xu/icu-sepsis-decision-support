# View Setup Procedure (No Materialized Views — Zero Extra Storage)

The scripts in `scripts/` create **regular views** (not materialized views). Views compute results on each query; they do **not** store data and add no extra storage to your database.

---

## Prerequisites

1. **MIMIC-IV base data** loaded in Postgres (`mimiciv_hosp`, `mimiciv_icu` schemas).
2. **mimic-code derived tables** in `mimiciv_derived` schema:
   - `age`, `icustay_detail`, `vitalsign`, `chemistry`, `coagulation`
   - Optional for prediction: `sofa_hourly` or `sofa` (from mimic-code)
3. **PostgreSQL client** (`psql`) and network access to your database.

---

## Step 1: Run Scripts in Order

Scripts must be run in dependency order. From the project root:

**Option A: Use the bash script (reads from `.env`):**

```bash
./run_setup_views.sh
```

Ensure `.env` has `DB_HOST`, `DB_USER`, `DB_NAME`, and `DB_PASSWORD` set.

---

**Option B: Run manually with psql:**

```bash
# Replace <RDS_ENDPOINT> with your AWS RDS hostname
# Get it with: cd terraform && terraform output -raw db_address

RDS_HOST="<your-rds-endpoint>"
DB_NAME="mimiciv"
DB_USER="postgres"
# You'll be prompted for password, or set PGPASSWORD

# Run each script in order:
psql -h $RDS_HOST -U $DB_USER -d $DB_NAME -f scripts/01_first_icu_stay.sql
psql -h $RDS_HOST -U $DB_USER -d $DB_NAME -f scripts/02_fis_icd9.sql
psql -h $RDS_HOST -U $DB_USER -d $DB_NAME -f scripts/03_fis_icd9_titled.sql
psql -h $RDS_HOST -U $DB_USER -d $DB_NAME -f scripts/04_fisi9t_profile.sql
psql -h $RDS_HOST -U $DB_USER -d $DB_NAME -f scripts/05_fisi9t_unique_patient_profile.sql
psql -h $RDS_HOST -U $DB_USER -d $DB_NAME -f scripts/06_fisi9t_vitalsign_hourly.sql
psql -h $RDS_HOST -U $DB_USER -d $DB_NAME -f scripts/07_fisi9t_procedureevents_hourly.sql
psql -h $RDS_HOST -U $DB_USER -d $DB_NAME -f scripts/08_fisi9t_chemistry_hourly.sql
psql -h $RDS_HOST -U $DB_USER -d $DB_NAME -f scripts/09_fisi9t_coagulation_hourly.sql
```

**One-liner** (you'll be prompted for password each time, or use `PGPASSWORD`):

```bash
for f in scripts/0*.sql scripts/09*.sql; do
  psql -h <RDS_ENDPOINT> -U postgres -d mimiciv -f "$f"
done
```

---

## Step 2: Update Environment Variables

1. **Copy the example file:**
   ```bash
   cp .env.example .env
   ```

2. **For AWS RDS, set these in `.env`:**

   | Variable | Where to get it |
   |----------|-----------------|
   | `DB_NAME` | `mimiciv` (or your DB name) |
   | `DB_USER` | `postgres` (or from `terraform output db_username`) |
   | `DB_PASSWORD` | From your `terraform.tfvars` (Terraform cannot output it) |
   | `DB_HOST` | `terraform output -raw db_address` |
   | `DB_PORT` | `5432` |
   | `DB_SCHEMA` | `mimiciv_derived` |

3. **Quick fill from Terraform** (then add password manually):
   ```bash
   cd terraform
   terraform output -raw env_file_content > ../.env
   # Edit ../.env and add: DB_PASSWORD=<your-password-from-terraform.tfvars>
   ```

---

## Step 3: Run the App

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Then open http://localhost:8000/patients/

---

## Verify

```bash
# Test a patient from cohort.py (e.g. first tuple)
curl "http://localhost:8000/patients/10021666/35475449/22756440/features/static"
```

If you see JSON with `sources.profile`, the views are working.

---

## Schema Note

Scripts assume:
- `mimiciv_icu.icustays` — raw MIMIC-IV ICU stays
- `mimiciv_hosp.diagnoses_icd`, `mimiciv_hosp.d_icd_diagnoses` — hosp tables
- `mimiciv_derived.age`, `icustay_detail`, `vitalsign`, `chemistry`, `coagulation` — from mimic-code

If your schema names differ, edit the scripts accordingly.
