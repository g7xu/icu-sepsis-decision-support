# SOFA Score & Sepsis Table Setup

Procedure for creating SOFA scores, sepsis-3 diagnoses, and related materialized views on the MIMIC-IV database.

## Prerequisites

- MIMIC-IV database loaded on PostgreSQL (AWS RDS or local)
- `.env` file with `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`
- `mimic-iv/` repo with SQL scripts (cloned from [MIT-LCP/mimic-code](https://github.com/MIT-LCP/mimic-code))
- All upstream MIMIC-IV derived tables already created (e.g., `icustay_hourly`, `bg`, `ventilation`, `vitalsign`, `gcs`, `enzyme`, `chemistry`, `complete_blood_count`, `urine_output_rate`, `epinephrine`, `norepinephrine`, `dopamine`, `dobutamine`, `antibiotic`)

## Setup

```bash
set -a && source .env && set +a
export PGPASSWORD=$DB_PASSWORD
```

## Steps

### Step 1: SOFA scores (slow — 10-30 min)

Creates `mimiciv_derived.sofa` (~8.2M rows). We use a chunked version that breaks the original monolithic query into 10 independent steps to avoid connection timeouts.

```bash
psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME \
  -f mimic-iv/concepts_postgres/score/sofa_chunked.sql
```

> **Note:** The original `sofa.sql` runs as a single massive CTE and will likely timeout over a remote connection. `sofa_chunked.sql` creates intermediate tables for each SOFA organ component (respiration, coagulation, liver, cardiovascular, CNS, renal), combines them, then cleans up.

### Step 2: Suspicion of infection (~1-3 min)

Creates `mimiciv_derived.suspicion_of_infection` (~950K rows).

```bash
psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME \
  -f mimic-iv/concepts_postgres/sepsis/suspicion_of_infection.sql
```

### Step 3: Sepsis-3 diagnoses (depends on steps 1 & 2)

Creates `mimiciv_derived.sepsis3` (~41K rows).

```bash
psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME \
  -f mimic-iv/concepts_postgres/sepsis/sepsis3.sql
```

### Step 4: Cohort SOFA hourly materialized view

Creates `mimiciv_derived.fisi9t_sofa_hourly` (~5K rows) — SOFA data filtered to our cohort patients.

```bash
psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME \
  -f scripts/10_fisi9t_sofa_hourly.sql
```

### Step 5: Feature matrix materialized view

Creates `mimiciv_derived.fisi9t_feature_matrix_hourly` (~5K rows) — wide table joining all hourly sources.

```bash
psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME \
  -f scripts/11_fisi9t_feature_matrix_hourly.sql
```

### Step 6: Re-populate simulation cache

Loads March 13 SOFA data into `simulation.sim_cache_sofa_hourly` (~650 rows).

```bash
python manage.py preload_cohort_cache
```

## Cleanup

```bash
unset PGPASSWORD
```

## Verification

```sql
SELECT 'sofa' AS tbl, COUNT(*) FROM mimiciv_derived.sofa
UNION ALL SELECT 'suspicion_of_infection', COUNT(*) FROM mimiciv_derived.suspicion_of_infection
UNION ALL SELECT 'sepsis3', COUNT(*) FROM mimiciv_derived.sepsis3
UNION ALL SELECT 'fisi9t_sofa_hourly', COUNT(*) FROM mimiciv_derived.fisi9t_sofa_hourly
UNION ALL SELECT 'fisi9t_feature_matrix_hourly', COUNT(*) FROM mimiciv_derived.fisi9t_feature_matrix_hourly
UNION ALL SELECT 'sim_cache_sofa_hourly', COUNT(*) FROM simulation.sim_cache_sofa_hourly;
```

Expected output:

| Table | Approximate Rows |
|-------|-----------------|
| `sofa` | ~8.2M |
| `suspicion_of_infection` | ~950K |
| `sepsis3` | ~41K |
| `fisi9t_sofa_hourly` | ~5K |
| `fisi9t_feature_matrix_hourly` | ~5K |
| `sim_cache_sofa_hourly` | ~650 |
