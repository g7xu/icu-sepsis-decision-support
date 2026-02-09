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

After RDS is created (typically 5-10 minutes), load the MIMIC-IV 3.1 data from your local `mimiciv/` folder.

### Step 1: Get database connection details

```bash
terraform output
# Or specifically:
terraform output connection_string
```

Save the endpoint, username, and password for the following steps.

### Step 2: Clone the MIMIC-code repository

The [MIMIC-code repo](https://github.com/MIT-LCP/mimic-code/tree/main/mimic-iv) has PostgreSQL scripts to create tables and load data.

```bash
git clone https://github.com/MIT-LCP/mimic-code.git
cd mimic-code/mimic-iv/buildmimic/postgres/
```

### Step 3: Connect to your RDS instance

Using `psql` (PostgreSQL client):

```bash
psql -h <RDS_ENDPOINT> -U postgres -d mimiciv
```

Enter your password from `terraform.tfvars` when prompted.

### Step 4: Load data using MIMIC scripts

The repo provides four SQL scripts to run **in order**:

1. **`create.sql`** – Creates all MIMIC-IV tables (hosp and icu schemas)
2. **`load_gz.sql`** – Loads compressed CSV files (edit paths first!)
3. **`constraint.sql`** – Adds primary/foreign keys
4. **`index.sql`** – Creates indexes for query performance

**Edit `load_gz.sql` first**: Update the data path to point to your local MIMIC-IV data:

```sql
-- Change paths like:
\COPY mimiciv_hosp.patients FROM '/path/to/mimiciv/3.1/hosp/patients.csv.gz' WITH (FORMAT CSV, HEADER, COMPRESSION GZIP);
```

Then run each script:

```bash
psql -h <RDS_ENDPOINT> -U postgres -d mimiciv -f create.sql
psql -h <RDS_ENDPOINT> -U postgres -d mimiciv -f load_gz.sql
psql -h <RDS_ENDPOINT> -U postgres -d mimiciv -f constraint.sql
psql -h <RDS_ENDPOINT> -U postgres -d mimiciv -f index.sql
```

**Note:** Loading takes **several hours** (especially `chartevents` with millions of rows).

### Step 5: Verify data loaded successfully

Connect and check row counts:

```sql
SELECT COUNT(*) FROM mimiciv_hosp.patients;
SELECT COUNT(*) FROM mimiciv_icu.icustays;
SELECT COUNT(*) FROM mimiciv_icu.chartevents;  -- This will be the largest table
```

Compare against expected counts from MIMIC-IV documentation.

### Step 6: Create derived schema and views

Create the `mimiciv_derived` schema and any materialized views your app needs (e.g., `fisi9t_unique_patient_profile`):

```sql
CREATE SCHEMA IF NOT EXISTS mimiciv_derived;
-- Then create your custom views/tables as needed
```

### Data Size Considerations

- **Compressed CSV files**: 10 GB (5.9 GB hosp + 4.1 GB icu)
- **Uncompressed in PostgreSQL**: ~128 GB total (100-120 GB data + 20-30% for indexes)
- **Largest table**: `chartevents` (3.3 GB compressed → ~40-50 GB uncompressed)
- **Your RDS config**: Starts at 20 GB (Free Tier), auto-scales up to 130 GB max

**How Auto-Scaling Works**:
- Starts at **20 GB** (Free Tier, $0/month)
- As you load MIMIC-IV data, RDS automatically increases storage in 10% increments
- Scales up to **130 GB** maximum when database is full
- **You only pay for storage after it scales beyond 20 GB**

**Cost Strategy**: Start loading data on 20 GB. When storage reaches ~18 GB (90% full), RDS auto-scales to ~22 GB and you start paying ~$0.23/month. As you continue loading, it scales incrementally until reaching the full 130 GB (~$13/month).

### Alternative: Manual load with COPY

If you prefer more control, load each table individually:

```bash
gunzip -c mimiciv/3.1/hosp/patients.csv.gz | \
  psql -h <ENDPOINT> -U postgres -d mimiciv \
  -c "\COPY mimiciv_hosp.patients FROM STDIN WITH (FORMAT CSV, HEADER TRUE);"
```

Repeat for each CSV file in `hosp/` and `icu/` directories.

---

## Restrict access

To allow only your IP:

- Set `allowed_cidr_blocks` in `terraform.tfvars` to e.g. `["YOUR_IP/32"]`.
- Run `terraform apply` to update the security group.

---

## Destroy (deletes RDS and all data)

```bash
cd terraform
terraform destroy
```

---

## Cost Estimates

**Current configuration (starts at 20 GB, auto-scales to 130 GB):**

- **Initial state (empty database)**:
  - Storage: 20 GB → **$0/month** (Free Tier)
  - Instance: db.t4g.micro → $0/month (Free Tier, 750 hours)
  - **Total: $0/month**

- **After loading MIMIC-IV (Year 1 with Free Tier)**:
  - Storage: ~130 GB → **~$13/month** (pay for 110 GB beyond free tier)
  - Instance: db.t4g.micro → $0/month (Free Tier)
  - **Total: ~$13/month or ~$156/year**

- **Year 2+ (after Free Tier expires)**:
  - Storage: 130 GB → ~$15/month
  - Instance: db.t4g.micro → ~$12/month
  - **Total: ~$27/month or ~$324/year**

**Cost-saving strategies:**
- **Load data gradually**: Storage auto-scales as you load, so you only pay for what you use
- **Load essential tables only**: Skip `chartevents` and `labevents` to stay under 20 GB → $0/month indefinitely
- **Stop when not in use**: Stop the instance to avoid compute charges (~$12/month savings after Year 1)
- **Apply for AWS credits**: AWS Educate ($100-200) or Research Credits ($5,000+) for qualified medical research projects

---

## Troubleshooting

### Terraform Issues

- **"No valid credential sources found"**: 
  - Run `aws configure` with IAM user access keys (not root user keys)
  - Verify credentials work: `aws sts get-caller-identity`
  - Note: `aws login` credentials may not work with Terraform; use IAM user keys instead

- **"Cannot find version X.X for postgres"**: 
  - Check available versions: `aws rds describe-db-engine-versions --engine postgres --query 'DBEngineVersions[].EngineVersion' --output text`
  - Update `db_engine_version` in `terraform.tfvars` to a supported version

- **"FreeTierRestrictionError" on backup retention**: 
  - Free Tier allows max 1 day backup retention
  - Set `backup_retention_period = 1` in `terraform.tfvars`

- **No default VPC**: Create a VPC or reference an existing one in the Terraform config.

- **Insufficient permissions**: Ensure AWS IAM user has RDS, EC2, and VPC permissions (e.g., attach `AdministratorAccess` or specific RDS/VPC policies).

- **DB instance already exists**: Check for an existing instance with the same identifier in the same region.

### Data Loading Issues

- **psql not found**: Install PostgreSQL client tools (`brew install postgresql` on macOS, or PostgreSQL Windows installer)

- **Connection timeout**: Verify your IP in `allowed_cidr_blocks` matches your current public IP (`curl ifconfig.me`)

- **Out of storage**: Current config allows auto-scaling from 20 GB to 130 GB. If you need more space, increase `max_allocated_storage` in `terraform.tfvars` and run `terraform apply` (each additional GB costs $0.115/month)

- **Load taking too long**: 
  - Run `create.sql` and `load_gz.sql` first (tables + data)
  - Skip `constraint.sql` and `index.sql` initially for faster loading
  - Add constraints/indexes later when you need query performance

---

## Later: full cloud deployment

When you want to run the app on AWS too:

1. Create a **Lightsail** instance (or small EC2).
2. Attach a **Lightsail static IP** (or Elastic IP on EC2) so the URL doesn’t change.
3. On the instance: install Python, clone repo, set env vars (or SSM), run Django with **gunicorn + nginx**.
4. Update the RDS security group so the Lightsail/EC2 instance can reach PostgreSQL (add its IP or security group to `allowed_cidr_blocks` or an ingress rule).

Detailed steps for Lightsail + gunicorn + nginx can be added here when you’re ready.
