# How to Run

## Option A: Docker (recommended)

```bash
# Start Django + Postgres
docker compose up --build

# Open in browser
open http://localhost:8000/patients/
```

## Option B: Local (Postgres must be running)

```bash
# Install deps
pip install -r requirements.txt

# Set DB vars (or use .env)
export DB_NAME=sepsis DB_USER=postgres DB_PASSWORD=postgres DB_HOST=localhost DB_PORT=5432

# Run server
python manage.py runserver
```

## Model Service + S3 Flow (External HTTPS on EC2)

The prediction endpoint (`GET /patients/<ids>/prediction`) now supports this flow:

1. Pull hourly-wide features from Postgres materialized views.
2. Select the **most recent hourly vector** at or before `as_of`.
3. Write that vector to S3 under:
   - `s3://<bucket>/<prefix>/patients/<subject_stay_hadm>/features/<hour>.json`
4. Load prior vectors from S3 (`MODEL_HISTORY_HOURS`).
5. Call EC2 model endpoint (`POST <MODEL_SERVICE_URL>/predict`).
6. Persist model output to S3 under:
   - `.../predictions/<hour>.json`
   - `.../io/<hour>.json` (request/response audit)

Comorbidity group behavior:
- First prediction must include `comorbidity_group`.
- Later calls may omit it; backend reuses the first stored group from S3.

Input vector rule (latest ERD):
- Preferred source is `fisi9t_feature_matrix_hourly` (or `mimiciv_derived.fisi9t_feature_matrix_hourly`) when present.
- If feature-matrix view is not present yet, backend falls back to multi-table source alignment.
- Backend requires patient rows in all required hourly matviews:
  - `vitals_hourly`
  - `procedures_hourly`
  - `chemistry_hourly`
  - `coagulation_hourly`
  - `sofa_hourly`
- It intersects by `(subject_id, stay_id, charttime_hour)` and selects the latest common hour at or before `as_of`.
- This ensures each model call includes source key triples from every required table.

**Configuration modes:**

When `MODEL_SERVICE_URL` is **empty** (default):
- Prediction endpoint returns an error: "Model service not configured"
- The UI will show "N/A" for risk score and "Not configured" for comorbidity group
- This prevents confusion from showing fake/stub data

When `MODEL_SERVICE_URL` is **set** (`.env`):
```bash
MODEL_SERVICE_URL=https://your-ec2-model-endpoint.example.com
MODEL_SERVICE_TIMEOUT=30
MODEL_SERVICE_API_KEY=optional_bearer_token

MODEL_S3_BUCKET=your-bucket-name
MODEL_S3_REGION=us-east-1
MODEL_S3_PREFIX=model-io
MODEL_HISTORY_HOURS=6
```

**S3 behavior:**
- Feature vectors and predictions are written to S3. If an object already exists (e.g. from a re-run), it is **not overwritten** to avoid duplicates.
- To start fresh and clear stale data: `python manage.py clear_model_s3`
- To wipe one patient only: `python manage.py clear_model_s3 --prefix model-io/patients/13129329_32482524_23992308`
- Dry run: `python manage.py clear_model_s3 --dry-run`

### EC2 model contract

The EC2 service must expose:

`POST /predict`

Request:
```json
{
  "patient": {"subject_id": 123, "stay_id": 456, "hadm_id": 789},
  "as_of": "2025-03-13T12:00:00",
  "current_feature_vector": {
    "vitals_hourly": {"subject_id": 123, "stay_id": 456, "charttime_hour": "...", "...": "..."},
    "procedures_hourly": {"subject_id": 123, "stay_id": 456, "charttime_hour": "...", "...": "..."},
    "chemistry_hourly": {"subject_id": 123, "stay_id": 456, "charttime_hour": "...", "...": "..."},
    "coagulation_hourly": {"subject_id": 123, "stay_id": 456, "charttime_hour": "...", "...": "..."},
    "sofa_hourly": {"subject_id": 123, "stay_id": 456, "charttime_hour": "...", "...": "..."}
  },
  "source_keys": {
    "vitals_hourly": {"subject_id": 123, "stay_id": 456, "charttime_hour": "..."},
    "procedures_hourly": {"subject_id": 123, "stay_id": 456, "charttime_hour": "..."},
    "chemistry_hourly": {"subject_id": 123, "stay_id": 456, "charttime_hour": "..."},
    "coagulation_hourly": {"subject_id": 123, "stay_id": 456, "charttime_hour": "..."},
    "sofa_hourly": {"subject_id": 123, "stay_id": 456, "charttime_hour": "..."}
  },
  "history_feature_vectors": [{"...": "..."}, {"...": "..."}]
}
```

Response:
```json
{
  "risk_score": 0.42,
  "comorbidity_group": "cardiovascular"
}
```

`comorbidity_group` can be omitted after first call for a patient.

### Required AWS setup

1. **Credentials**
   - Local: export `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and optionally `AWS_SESSION_TOKEN`.
   - EC2/ECS: attach IAM role.
2. **IAM permissions**
   - `s3:PutObject`, `s3:GetObject`, `s3:ListBucket` on your bucket/prefix.
3. **Bucket policy/CORS**
   - Ensure backend host can read/write required prefix.
4. **Network**
   - Backend must be able to reach EC2 HTTPS endpoint.
5. **TLS**
   - Use valid certificate on EC2 endpoint URL.

## Test the prediction endpoint

```bash
# Without MODEL_SERVICE_URL configured (will return error)
curl "http://localhost:8000/patients/10000032/39553978/29079034/prediction?as_of=2025-03-13T12:00:00&window_hours=24"
# Response: {"error": "Model service not configured. Set MODEL_SERVICE_URL in .env to enable predictions."}

# With MODEL_SERVICE_URL configured (requires EC2 + S3 setup in .env)
curl "http://localhost:8000/patients/10000032/39553978/29079034/prediction?as_of=2025-03-13T12:00:00&window_hours=24"
```

## Test the feature endpoints

```bash
# Static features
curl "http://localhost:8000/patients/10000032/39553978/29079034/features/static"

# Hourly features
curl "http://localhost:8000/patients/10000032/39553978/29079034/features/hourly?as_of=2025-03-13T12:00:00&window_hours=24"

# Hourly-wide (merged table for ML)
curl "http://localhost:8000/patients/10000032/39553978/29079034/features/hourly-wide?as_of=2025-03-13T12:00:00&window_hours=24"
```

Replace `10000032/39553978/29079034` with real `subject_id/stay_id/hadm_id` from your database.

## Similarity search (prediction view)

To show "3 most similar patients" when viewing a prediction:

1. Build materialized views (scripts 05, 06, 08, 09, 10, 11).
2. Export non-cohort feature matrix: `python manage.py export_similarity_matrix`
3. See [docs/SIMILARITY_SETUP.md](docs/SIMILARITY_SETUP.md) for full steps and implementation notes.
