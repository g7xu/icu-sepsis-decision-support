# Similarity Search Setup (Prediction View)

Find the 3 most similar patients (by feature vector) from outside the demo cohort when viewing a prediction. Used for "similar cases" cards in the prediction view.

## Quick reference

```bash
# 1. Build materialized views (if not already done)
psql -h localhost -p 5432 -U postgres -d mimiciv -f scripts/05_fisi9t_unique_patient_profile.sql
psql -h localhost -p 5432 -U postgres -d mimiciv -f scripts/06_fisi9t_vitalsign_hourly.sql
psql -h localhost -p 5432 -U postgres -d mimiciv -f scripts/08_fisi9t_chemistry_hourly.sql
psql -h localhost -p 5432 -U postgres -d mimiciv -f scripts/09_fisi9t_coagulation_hourly.sql
psql -h localhost -p 5432 -U postgres -d mimiciv -f scripts/10_fisi9t_sofa_hourly.sql
psql -h localhost -p 5432 -U postgres -d mimiciv -f scripts/11_fisi9t_feature_matrix_hourly.sql

# 2. Export non-cohort feature matrix to CSV
python manage.py export_similarity_matrix
```

## Prerequisites

1. **Materialized views** – Run scripts 05, 06, 08, 09, 10, 11 in order:
   ```bash
   cd /path/to/icu-sepsis-decision-support
   psql -h localhost -p 5432 -U postgres -d mimiciv -f scripts/05_fisi9t_unique_patient_profile.sql
   psql -h localhost -p 5432 -U postgres -d mimiciv -f scripts/06_fisi9t_vitalsign_hourly.sql
   psql -h localhost -p 5432 -U postgres -d mimiciv -f scripts/08_fisi9t_chemistry_hourly.sql
   psql -h localhost -p 5432 -U postgres -d mimiciv -f scripts/09_fisi9t_coagulation_hourly.sql
   psql -h localhost -p 5432 -U postgres -d mimiciv -f scripts/10_fisi9t_sofa_hourly.sql
   psql -h localhost -p 5432 -U postgres -d mimiciv -f scripts/11_fisi9t_feature_matrix_hourly.sql
   ```

2. **Cohort defined** – `patients/cohort.py` must have `PATIENT_STAYS` set (the 51 demo patients).

## Step 1: Export non-cohort feature matrix to CSV

Excludes the 51 cohort patients; exports all other patients' hourly feature vectors.

```bash
python manage.py export_similarity_matrix
# Output: static/similarity_matrix.csv (or path from SIMILARITY_CSV_PATH in .env)

# Or specify output path:
python manage.py export_similarity_matrix --output static/similarity_matrix.csv
```

**When to re-run:** After changing `PATIENT_STAYS` in cohort.py, or after refreshing the feature matrix (re-running script 11).

## Step 2: Configure CSV path (optional)

Set in `.env` if using a custom path:

```
SIMILARITY_CSV_PATH=static/similarity_matrix.csv
```

Default: `static/similarity_matrix.csv` (relative to project root). The path is read from the `SIMILARITY_CSV_PATH` environment variable.

## Step 3: Similarity at prediction view open

When the user opens the prediction view (`/patients/<id>/prediction-view/`):

1. Get the **current patient's feature vector** used for the most recent sepsis prediction (same format as model input).
2. Load the CSV (or query DB) for non-cohort feature vectors.
3. Compute **cosine similarity** (or Jaccard) between current vector and each row.
4. Take the **top 3** most similar.
5. For those 3: fetch profile (script 5 / `fisi9t_unique_patient_profile`) and sepsis outcome (`sepsis3`).
6. Render as cards with sepsis outcome.

**Implementation notes:**
- Similarity runs **locally** (no external API).
- Trigger: on page load (EventList / DOMContentLoaded) when prediction view opens.
- Use numeric feature columns only for similarity; ignore `subject_id`, `stay_id`, `charttime_hour` in the distance computation.
- Handle NaN/null: fill with 0 or exclude from cosine.

## CSV format

| Column | Description |
|--------|-------------|
| subject_id, stay_id, hadm_id | Patient identifiers |
| charttime_hour | Hour of the vector |
| heart_rate, sbp, dbp, ... | Feature columns (same as feature matrix) |
| sofa_24hours, etc. | SOFA components |

One row per (subject_id, stay_id, charttime_hour). Multiple rows per patient (one per hour of stay).

## Cosine similarity

For vectors **a** (current patient) and **b** (candidate):

```
cosine_sim(a, b) = (a · b) / (||a|| * ||b||)
```

Use scipy or numpy. Fill NaN with 0 before computing.
