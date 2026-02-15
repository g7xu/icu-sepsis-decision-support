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

## Model Service (External HTTPS)

The prediction endpoint calls an external model service when `MODEL_SERVICE_URL` is set.

**Stub mode (default):** Leave `MODEL_SERVICE_URL` empty. Predictions use deterministic stub data.

**Live mode:** Set in `.env`:
```
MODEL_SERVICE_URL=https://your-model-service.example.com
MODEL_SERVICE_TIMEOUT=30
MODEL_SERVICE_API_KEY=optional_bearer_token
```

**Model contract:** The service must expose `POST /predict`:

Request:
```json
{
  "patient": {"subject_id": 123, "stay_id": 456, "hadm_id": 789},
  "as_of": "2025-03-13T12:00:00",
  "features": {"hourly_wide": [...], "columns": [...]}
}
```

Response:
```json
{
  "risk_score": 0.42,
  "comorbidity_group": "cardiovascular"
}
```

## Test the prediction endpoint

```bash
# Stub mode (MODEL_SERVICE_URL empty)
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
