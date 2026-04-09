# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ICU Sepsis Decision Support — a Django web app providing clinicians with interpretable ML-based sepsis risk predictions and similar-patient comparisons. It uses a cohort of 51 curated MIMIC-IV ICU patients with a simulation clock that steps through their hospital stay hour-by-hour.

## Commands

### Local Development (Docker — recommended)
```bash
docker compose up --build         # First run: build image + start web + db
docker compose up                 # Subsequent runs
docker compose exec web python manage.py migrate
docker compose exec web python manage.py runserver
```

### Without Docker (PostgreSQL already running)
```bash
pip install -r requirements.txt
cp .env.example .env              # Edit DB_HOST=localhost and credentials
python manage.py migrate
python manage.py runserver        # http://localhost:8000/patients/
```

### Custom Management Commands
```bash
python manage.py export_similarity_matrix          # Export feature matrix CSV for similarity search
python manage.py clear_model_s3 [--prefix PREFIX] [--dry-run]  # Clear S3 model IO artifacts
```

### Database Setup (one-time, after loading MIMIC-IV 3.1 data)
Run numbered SQL scripts in order from `scripts/01_*.sql` through `scripts/11_*.sql` to build the materialized views the app depends on.

## Architecture

### Django App Structure
The single Django app is `patients/`. Key files:

- **`patients/cohort.py`** — Defines `PATIENT_STAYS` (the 51 demo patients) and `get_cohort_filter()`. This is the boundary between "demo cohort" and the full MIMIC-IV population.
- **`patients/models.py`** — Read-only ORM models mapped directly to PostgreSQL materialized views (no writes, no migrations touch these). Models: `UniquePatientProfile`, `VitalsignHourly`, `ChemistryHourly`, `CoagulationHourly`, `SofaHourly`, `ProcedureeventsHourly`.
- **`patients/services.py`** — All data-fetching logic: queries materialized views, calls the external ML model service over HTTPS, loads the similarity matrix CSV, and writes/reads S3 audit artifacts.
- **`patients/api.py`** — JSON REST endpoints consumed by the frontend: `features/static`, `features/hourly`, `features/hourly-wide`, `feature-bundle`, `prediction`, `similar-patients`.
- **`patients/views.py`** — HTML views for patient list, detail, and prediction. The simulation clock (current ICU hour) is stored in Django sessions; prediction results are also cached in session.
- **`config/settings.py`** — Django settings. Session backend is `django.contrib.sessions.backends.db` (persists across restarts). WhiteNoise serves static files.

### Data Flow
1. **Patient list** → renders cohort from `cohort.py`
2. **Simulation clock advance** → session hour increments → triggers prediction fetch
3. **Prediction request** → `api.py` → `services.get_prediction()` → queries `fisi9t_feature_matrix_hourly` (pre-merged wide table, 1 row/hour) → optionally writes features to S3 → POSTs to EC2 model service `/predict` → caches result in session + S3
4. **Similar patients** → `services.py` loads `static/similarity_matrix.csv` → cosine similarity → top 3 non-cohort candidates

### External Dependencies
- **ML Model Service** (EC2, optional): `POST /predict` — receives feature vector + 6-hour history, returns `{risk_score: float, comorbidity_group: string}`. Configured via `MODEL_SERVICE_URL`. If unset, predictions are disabled.
- **AWS S3** (optional): audit trail of features and predictions under `s3://<bucket>/<prefix>/patients/<subject_stay_hadm>/`.
- **PostgreSQL**: MIMIC-IV 3.1 dataset + derived materialized views. All materialized view names are prefixed `fisi9t_`.

### Infrastructure
- **`terraform/`** — Provisions AWS RDS PostgreSQL + security groups. Run `terraform apply` then `terraform output -raw env_file_content > ../.env` to auto-generate `.env`.
- **`Dockerfile`** — `python:3.11-slim` + `gcc`/`libpq-dev`, runs `collectstatic` at build time.
- **`docker-compose.yml`** — `web` (Django on port 8000) + `db` (PostgreSQL).

## Key Environment Variables

| Variable | Purpose |
|---|---|
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | PostgreSQL connection |
| `DB_SCHEMA` | Schema containing materialized views (typically `mimiciv_derived`) |
| `SECRET_KEY` | Django secret key |
| `MODEL_SERVICE_URL` | HTTPS endpoint for EC2 ML model; leave empty to disable predictions |
| `MODEL_HISTORY_HOURS` | Hours of feature history sent to model (default: 6) |
| `MODEL_S3_BUCKET`, `MODEL_S3_PREFIX`, `MODEL_S3_REGION` | S3 audit trail config |
| `SIMILARITY_CSV_PATH` | Path to similarity matrix CSV (default: `static/similarity_matrix.csv`) |

Copy `.env.example` to `.env` to see all available variables.

## Docs
- `docs/RUNNING.md` — detailed run instructions, model service API contract, S3 flow
- `docs/SIMILARITY_SETUP.md` — building materialized views, exporting similarity matrix CSV
- `terraform/README.md` — MIMIC-IV data loading (4-step process, takes 4–8 hours)
