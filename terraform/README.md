# Terraform: RDS PostgreSQL for MIMIC-IV

This folder creates an RDS PostgreSQL instance for the ICU Sepsis Decision Support app. The app runs **locally**; only the database is on AWS.

---

## Prerequisites

- **AWS Account**: Sign up at [aws.amazon.com](https://aws.amazon.com)
- **AWS CLI** installed and configured:
  - Install: `brew install awscli` (macOS) or download from [AWS](https://aws.amazon.com/cli/)
  - Configure with IAM user access keys: `aws configure`
  - **Important**: Create an IAM user (not root) with RDS/VPC permissions, then use that user's access keys
  - Verify: `aws sts get-caller-identity`
- **Terraform** >= 1.0: [install](https://www.terraform.io/downloads)
  - macOS: `brew install terraform`
  - Windows: `choco install terraform`
- **PostgreSQL client** (psql) for loading data:
  - macOS: `brew install postgresql`
  - Windows: [PostgreSQL installer](https://www.postgresql.org/download/windows/)

---

## Setup

1. **Copy the example variables file**
   ```bash
   cd terraform
   copy terraform.tfvars.example terraform.tfvars   # Windows
   # cp terraform.tfvars.example terraform.tfvars  # Linux/macOS
   ```

2. **Edit `terraform.tfvars`**
   - `db_password` – strong password for RDS
   - `allowed_cidr_blocks` – your IP so your local machine can reach RDS (e.g. `["YOUR_IP/32"]`). Avoid `["0.0.0.0/0"]` in production.

3. **Create the database**
   ```bash
   terraform init
   terraform plan
   terraform apply
   ```
   Type `yes` when prompted.

4. **Get connection details**
   ```bash
   terraform output
   ```
   Set in your local `.env`: `DB_HOST=<db_address>`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_PORT`, `DB_SCHEMA=mimiciv_derived`.

---

## Outputs

After `terraform apply`, get connection details with `terraform output`:

| Output | Description |
|--------|-------------|
| `db_endpoint` | Full endpoint (hostname:port) |
| `db_address` | Hostname only (use for `DB_HOST`) |
| `db_port` | Port number (5432) |
| `db_name` | Database name (mimiciv) |
| `db_username` | Database username (sensitive) |
| `connection_string` | Full PostgreSQL connection string (sensitive) |
| `env_file_content` | Ready-to-use `.env` file content (sensitive) |
| `security_group_id` | Security group ID for updating access rules |

**Example**: Save `.env` for your Django app:

```bash
terraform output -raw env_file_content > ../.env
```

---

## Load MIMIC-IV Data

After RDS is created (typically 5-10 minutes), follow these detailed steps to load the complete MIMIC-IV 3.1 dataset from your local machine to AWS RDS.

### Prerequisites Verification

Before starting, verify you have everything ready:

```bash
# 1. Test RDS connection (get endpoint from terraform output)
terraform output db_address
psql -h <RDS_ENDPOINT> -U postgres -d mimiciv
# Enter password from terraform.tfvars when prompted
# Type \q to exit after successful connection

# 2. Verify your local MIMIC-IV data location (you need to request for mimic data)
ls -lh ~/Documents/local_repo/icu-sepsis-decision-support/mimiciv/3.1/hosp/
ls -lh ~/Documents/local_repo/icu-sepsis-decision-support/mimiciv/3.1/icu/

# 3. Confirm required tools are installed
which psql gzip
psql --version  # Should be v10 or later
```

---

### Step 1: Clone MIMIC-code Repository

Get the official PostgreSQL loading scripts:

```bash
git clone https://github.com/MIT-LCP/mimic-code.git
cd mimic-code/mimic-iv/buildmimic/postgres/
ls -lh *.sql
```

You should see four SQL scripts:
- `create.sql` - Table schemas
- `load_gz.sql` - Data loading commands
- `constraint.sql` - Primary/foreign keys
- `index.sql` - Performance indexes

---

### Step 2: Understand the Load Script

**How it works:**
- Script expects a variable: `mimic_data_dir`
- You pass it when running psql with: `-v mimic_data_dir=<YOUR_PATH>`
- Script uses `\cd` to navigate into hosp/ and icu/ subdirectories
- Loads files using relative paths (e.g., `admissions.csv.gz`)

**Preview the script (optional):**
```bash
head -20 load_gz.sql
```

You'll see:
```sql
\cd :mimic_data_dir
\cd hosp
\COPY mimiciv_hosp.admissions FROM PROGRAM 'gzip -dc admissions.csv.gz' ...
```

---

### Step 3: Create Database Schema

Create all table structures:

```bash
psql -h <RDS_ENDPOINT> -U postgres -d mimiciv -f create.sql
```

**Expected output:**
- Creates `mimiciv_hosp` and `mimiciv_icu` schemas
- Creates ~30 empty tables
- **Duration:** 2-5 minutes

**Verify schemas created:**
```bash
psql -h <RDS_ENDPOINT> -U postgres -d mimiciv -c "\dn"
psql -h <RDS_ENDPOINT> -U postgres -d mimiciv -c "\dt mimiciv_hosp.*"
psql -h <RDS_ENDPOINT> -U postgres -d mimiciv -c "\dt mimiciv_icu.*"
```

---

### Step 4: Load Data (Long Running - Can Run Overnight)

**This is the main loading step that takes several hours.**

Run with the `-v` flag to pass your data directory path:

```bash
psql -h <RDS_ENDPOINT> \
     -U postgres \
     -d mimiciv \
     -v mimic_data_dir=/Users/guoxuanxu/Documents/local_repo/icu-sepsis-decision-support/mimiciv/3.1 \
     -f load_gz.sql
```

**Expected behavior:**
- Streams compressed CSV files from your Mac → AWS RDS
- Decompresses on-the-fly (no need to unzip files)
- Loads tables sequentially (hosp tables first, then icu tables)
- **Duration:** 4-8 hours depending on internet upload speed
- **Safe to run overnight:** Process is automatic and unattended

**What happens:**
1. Loads small tables first (~100 MB each): patients, admissions, etc.
2. Loads medium tables (~1-5 GB): prescriptions, procedures, etc.
3. Loads large tables last:
   - `labevents` (~2.4 GB compressed → ~30 GB uncompressed)
   - `chartevents` (~3.3 GB compressed → ~40 GB uncompressed)

**Monitoring progress (optional):**

Open a **new terminal** while loading continues:

```bash
# Check which tables are populated
psql -h <RDS_ENDPOINT> -U postgres -d mimiciv -c "
SELECT schemaname, tablename, 
       pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size
FROM pg_tables 
WHERE schemaname IN ('mimiciv_hosp', 'mimiciv_icu')
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;"

# Watch storage auto-scale in real-time
watch -n 60 "aws rds describe-db-instances \
  --db-instance-identifier icu-sepsis-db \
  --query 'DBInstances[0].AllocatedStorage'"

# Check database size growth
psql -h <RDS_ENDPOINT> -U postgres -d mimiciv -c \
  "SELECT pg_size_pretty(pg_database_size('mimiciv'));"
```

**Storage auto-scaling during load:**
- Starts at **20 GB** (Free Tier)
- At ~18 GB (90% full) → scales to **22 GB**
- Continues scaling in 10% increments
- Final size: **~128-130 GB**

---

### Step 5: Add Constraints

After data is loaded, add primary keys and foreign keys:

```bash
psql -h <RDS_ENDPOINT> -U postgres -d mimiciv -f constraint.sql
```

**Purpose:**
- Enforces data integrity
- Required for proper relational database structure
- Prevents invalid data insertion

**Expected output:**
- Creates primary keys on ID columns
- Creates foreign key relationships between tables
- **Duration:** 20-30 minutes

---

### Step 6: Create Indexes

Build indexes for fast query performance:

```bash
psql -h <RDS_ENDPOINT> -U postgres -d mimiciv -f index.sql
```

**Purpose:**
- Dramatically improves query speed
- Creates indexes on commonly queried columns
- Essential for production use

**Expected output:**
- Creates indexes on patient IDs, admission IDs, timestamps, etc.
- Adds 20-30% to database size
- **Duration:** 1-2 hours

---

### Step 7: Verify Data Load Success

Connect and validate the data:

```bash
psql -h <RDS_ENDPOINT> -U postgres -d mimiciv
```

**Run verification queries:**

```sql
-- Check row counts for key tables
SELECT 'patients' as table_name, COUNT(*) FROM mimiciv_hosp.patients
UNION ALL
SELECT 'admissions', COUNT(*) FROM mimiciv_hosp.admissions
UNION ALL
SELECT 'icustays', COUNT(*) FROM mimiciv_icu.icustays
UNION ALL
SELECT 'chartevents', COUNT(*) FROM mimiciv_icu.chartevents
UNION ALL
SELECT 'labevents', COUNT(*) FROM mimiciv_hosp.labevents;

-- Check final database size
SELECT pg_size_pretty(pg_database_size('mimiciv'));
-- Expected: ~128 GB

-- Check schema sizes
SELECT schemaname, 
       pg_size_pretty(sum(pg_total_relation_size(schemaname||'.'||tablename))::bigint) as size
FROM pg_tables 
WHERE schemaname IN ('mimiciv_hosp', 'mimiciv_icu')
GROUP BY schemaname;

-- List all tables with row counts
SELECT schemaname, tablename, 
       pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size
FROM pg_tables 
WHERE schemaname IN ('mimiciv_hosp', 'mimiciv_icu')
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC
LIMIT 10;
```

**Expected row counts (MIMIC-IV 3.1):**
- `patients`: ~299,712
- `admissions`: ~431,231
- `icustays`: ~73,181
- `chartevents`: ~313,645,063 (313 million - largest table)
- `labevents`: ~122,103,667 (122 million)
