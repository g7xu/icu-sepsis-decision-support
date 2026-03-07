# Database Setup

All modes (including demo) require MIMIC-IV clinical data in a PostgreSQL database. This guide covers provisioning, loading data, and creating the views the application needs.

## Prerequisites

- [MIMIC-IV access](https://physionet.org/content/mimiciv/3.1/) — requires a PhysioNet credentialed account (approved data use agreement)
- PostgreSQL client (`psql`): macOS `brew install postgresql`, Windows [installer](https://www.postgresql.org/download/windows/)
- MIMIC-IV 3.1 data files downloaded locally (~128 GB total)

---

## 1. Provision the Database

Choose one option:

### Option A: Local PostgreSQL

1. Install PostgreSQL locally
2. Create the database:
   ```bash
   createdb mimiciv
   ```
3. Load MIMIC-IV data using the vendored scripts in `scripts/buildmimic/` (see [Loading MIMIC-IV Data](#2-load-mimic-iv-data) below)

### Option B: AWS RDS

#### Prerequisites

- **AWS CLI** installed and configured (`aws configure` with IAM user access keys)
  - Verify: `aws sts get-caller-identity`
- **Terraform** >= 1.0: macOS `brew install terraform`, Windows `choco install terraform`

> **Note:** `terraform apply` provisions the full AWS stack (RDS, EC2, and ECR) together. If you only need the database for local development, the EC2 and ECR resources will still be created but can be ignored — they incur minimal cost when idle.

#### Provision

1. **Configure Terraform variables**
   ```bash
   cd terraform
   cp terraform.tfvars.example terraform.tfvars
   ```

2. **Edit `terraform.tfvars`**:
   - `db_password` — strong password for RDS
   - `allowed_cidr_blocks` — your IP (e.g. `["YOUR_IP/32"]`). Avoid `["0.0.0.0/0"]` in production.

3. **Create the database**
   ```bash
   terraform init
   terraform plan
   terraform apply    # type 'yes' when prompted
   ```

4. **Get connection details**
   ```bash
   terraform output
   ```

#### Terraform Outputs

| Output | Description |
|--------|-------------|
| `db_address` | Hostname (use for `DB_HOST`) |
| `db_endpoint` | Full endpoint (hostname:port) |
| `db_name` | Database name (`mimiciv`) |
| `db_username` | Database username (sensitive) |
| `connection_string` | Full PostgreSQL connection string (sensitive) |
| `env_file_content` | Ready-to-use `.env` content (sensitive) |

**Quick `.env` setup from Terraform:**
```bash
terraform output -raw env_file_content > ../.env
# Then edit ../.env and add: DB_PASSWORD=<your-password-from-terraform.tfvars>
```

---

## 2. Load MIMIC-IV Data

> **Duration:** 4-8 hours depending on internet/disk speed. Safe to run overnight.

The SQL scripts for loading MIMIC-IV data are included in this repo at `scripts/buildmimic/`. These are from [MIT-LCP/mimic-code](https://github.com/MIT-LCP/mimic-code) (MIT License).

You need four SQL scripts: `create.sql`, `load_gz.sql`, `constraint.sql`, `index.sql`.

### Create tables

```bash
psql -h <DB_HOST> -U postgres -d mimiciv -f scripts/buildmimic/create.sql
```
Creates `mimiciv_hosp` and `mimiciv_icu` schemas with ~30 empty tables (2-5 minutes).

### Load data

```bash
psql -h <DB_HOST> \
     -U postgres \
     -d mimiciv \
     -v mimic_data_dir=/path/to/mimiciv/3.1 \
     -f scripts/buildmimic/load_gz.sql
```

This streams compressed CSVs into the database. Largest tables: `chartevents` (~40 GB), `labevents` (~30 GB).

**Monitor progress** (in a separate terminal):
```bash
psql -h <DB_HOST> -U postgres -d mimiciv -c "
SELECT schemaname, tablename,
       pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size
FROM pg_tables
WHERE schemaname IN ('mimiciv_hosp', 'mimiciv_icu')
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;"
```

### Add constraints and indexes

```bash
# Primary/foreign keys (20-30 minutes)
psql -h <DB_HOST> -U postgres -d mimiciv -f scripts/buildmimic/constraint.sql

# Performance indexes (1-2 hours)
psql -h <DB_HOST> -U postgres -d mimiciv -f scripts/buildmimic/index.sql
```

### Verify

```sql
-- Expected row counts (MIMIC-IV 3.1)
SELECT 'patients' as table_name, COUNT(*) FROM mimiciv_hosp.patients
UNION ALL SELECT 'admissions', COUNT(*) FROM mimiciv_hosp.admissions
UNION ALL SELECT 'icustays', COUNT(*) FROM mimiciv_icu.icustays;
-- patients: ~299,712 | admissions: ~431,231 | icustays: ~73,181

SELECT pg_size_pretty(pg_database_size('mimiciv'));
-- Expected: ~128 GB
```

---

## 3. Create Application Views

The SQL scripts in `scripts/views/` create **regular views** (not materialized views) in the `mimiciv_derived` schema. Views compute on each query and add no extra storage.

### Prerequisites

These must already exist in your database:
- `mimiciv_hosp`, `mimiciv_icu` schemas (from MIMIC-IV base data)
- `mimiciv_derived` schema with: `age`, `icustay_detail`, `vitalsign`, `chemistry`, `coagulation` (from mimic-code derived tables)
- `sofa_hourly` or `sofa` (from mimic-code, used for prediction and SOFA charts)

### Run the setup script

Run the bash script (reads `DB_HOST`, `DB_USER`, etc. from `.env`):
```bash
./run_setup_views.sh
```

### Schema note

Scripts assume standard MIMIC-IV schema names (`mimiciv_icu`, `mimiciv_hosp`, `mimiciv_derived`). If yours differ, edit the scripts accordingly.

---

## 4. Run Migrations and Preload Cache

```bash
python manage.py migrate
python manage.py preload_cohort_cache
```

This creates the `sim_*` and `sim_cache_*` tables Django uses for the simulation.

### Verify

```bash
curl "http://localhost:8000/patients/10021666/35475449/22756440/features/static"
```

If you see JSON with `sources.profile`, the setup is complete.
