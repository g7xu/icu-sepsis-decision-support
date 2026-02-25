# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Django web application for ICU sepsis early warning. It reads from a MIMIC-IV PostgreSQL database (materialized views), runs a time-stepped ICU simulation, serves ML feature bundles via JSON API, and runs sepsis prediction in-process via joblib-loaded sklearn artifacts.

**Stack**: Python 3.11 · Django 4.2 · PostgreSQL 14 · Docker Compose · joblib · scikit-learn · D3.js v7 (frontend charts)

## Commands

### Docker (recommended)
```bash
docker compose up --build          # Start Django + Postgres (port 8000)
docker compose up                  # Start without rebuilding
docker compose down                # Stop
```

### Local dev (Postgres must already be running)
```bash
pip install -r requirements.txt
export DB_NAME=sepsis DB_USER=postgres DB_PASSWORD=postgres DB_HOST=localhost DB_PORT=5432
python manage.py runserver
```

### Django management
```bash
python manage.py migrate                # Apply Django auth/session + simulation table migrations
python manage.py preload_cohort_cache   # Pre-populate sim_cache_* tables from MIMIC (run once after setup)
python manage.py shell                  # Open Django shell
python manage.py check                  # Validate settings/config
```

### Curl test recipes
```bash
# Prediction (uses in-process model if artifacts present, else stub)
curl "http://localhost:8000/patients/10000032/39553978/29079034/prediction?as_of=2025-03-13T12:00:00&window_hours=24"

# Static features
curl "http://localhost:8000/patients/10000032/39553978/29079034/features/static"

# Hourly-wide ML table
curl "http://localhost:8000/patients/10000032/39553978/29079034/features/hourly-wide?as_of=2025-03-13T12:00:00&window_hours=24"

# Simulation control
curl -X POST http://localhost:8000/patients/advance-time/
curl -X POST http://localhost:8000/patients/rewind-time/
curl -X POST http://localhost:8000/patients/play/
curl -X POST http://localhost:8000/patients/pause/
curl -X POST http://localhost:8000/patients/reset/
curl http://localhost:8000/patients/simulation-status/
```

## Architecture

### Database: two-tier (read-only MIMIC + writable simulation)

**MIMIC-IV materialized views** (read-only, `managed=False` in Django):
All raw clinical data comes from pre-existing MIMIC-IV PostgreSQL materialized views in the `mimiciv_derived` schema (controlled by `DB_SCHEMA` env var). Django never writes to these views.

Required materialized views (created by SQL in `scripts/`):
- `fisi9t_unique_patient_profile` — patient index (demographics, ICU stay times)
- `fisi9t_vitalsign_hourly` — hourly vitals
- `fisi9t_procedureevents_hourly` — hourly procedures
- `fisi9t_chemistry_hourly` — hourly labs
- `fisi9t_coagulation_hourly` — hourly coag panel
- `sofa_hourly` (or `sofa`) — hourly SOFA scores

**Simulation tables** (writable, `managed=True` in Django, `simulation` schema):
Django manages these tables via migrations. They are populated incrementally by `pipeline.py` as the simulation advances:
- `sim_patients` — admitted patients (one row per admission)
- `sim_vitalsign_hourly` — hourly vitals
- `sim_procedureevents_hourly` — hourly procedures
- `sim_chemistry_hourly` — hourly chemistry labs
- `sim_coagulation_hourly` — hourly coagulation labs
- `sim_sofa_hourly` — hourly SOFA scores

**Cache tables** (writable, pre-populated by `preload_cohort_cache` management command):
Small (~60-row) indexed snapshots of MIMIC data for the March 13 cohort. `pipeline.py` reads from these during simulation to avoid full-table scans of the large MIMIC views:
- `sim_cache_patients`, `sim_cache_vitalsign_hourly`, `sim_cache_procedureevents_hourly`, etc.

### Dynamic table resolution in `services.py`
`services.py` maintains a `DERIVED_TABLE_CANDIDATES` dict of fallback table names. `_pick_first_existing()` probes Postgres at runtime and returns the first match (simulation tables are tried before MIMIC views). All data fetching goes through `_fetch_rows()` which runs raw parameterized SQL (Django cursor, `%(name)s` style).

### Patient identity triple
Every patient is uniquely identified by `(subject_id, stay_id, hadm_id)`. All URL patterns, ORM filters, and API responses use this triple. Django's composite PK limitation is worked around by using `subject_id` as the ORM primary key while always filtering on all three fields.

### Simulation clock (in-memory + background thread)
`patients/views.py` holds a module-level `_simulation` dict:
- `current_hour`: −1 (not started) to 23 (23:00)
- `auto_play`: Boolean for auto-advance loop
- `speed_seconds`: Real seconds per simulated hour (default 5.0)
- `direction`: `'forward'` or `'backward'`
- `_thread`: Background thread handle for auto-play

The simulation dock (fixed top-right UI in `base.html`) exposes these controls:
- **+1 / −1**: Manual single-step forward or backward
- **Play / Pause**: Auto-advance in background thread at the configured speed
- **Speed selector**: 1 / 5 / 10 / 30 seconds per simulated hour
- **Reset**: Clears all `sim_*` tables and resets clock to −1

The simulation is scoped to **March 13** data only (patients admitted on month=3, day=13). State resets on server restart.

### Pipeline (`patients/pipeline.py`)
`advance_hour(current_hour)` and `rewind_hour(current_hour)` are the core simulation primitives:
- `advance_hour`: reads the next hour's data from `sim_cache_*` tables, bulk-inserts into `sim_*` tables
- `rewind_hour`: deletes the last hour's data from `sim_*` tables
- These are called by `views.py` on each clock tick; each call processes ~60 rows (one per cohort patient)

### Cohort configuration
Edit `patients/cohort.py` to change which patients appear in the simulation:
- `PATIENT_STAYS` — list of `(subject_id, stay_id, hadm_id)` tuples (takes priority; currently 60 hardcoded March 13 patients)
- `SUBJECT_IDS` — simpler list of `subject_id` integers
- Set both to empty/`None` to show all patients

### Prediction pipeline (in-process joblib)
Predictions run inside the Django process. No external model API or S3.

- **Artifacts**: Place `sepsis_rf_pipeline.joblib` and `feature_cols.joblib` in `patients/model_artifacts/` (or set `MODEL_ARTIFACTS_DIR`). They are loaded once at startup in `patients/apps.PatientsConfig.ready()` via `model_local.load_model()`.
- **When artifacts are present**: `get_prediction()` in `services.py` fetches source tables, builds the current feature vector with `_build_current_vector_from_sources()`, then calls `model_local.predict(current_row)` to run the sklearn pipeline and return `risk_score` and `latent_class`.
- **When artifacts are missing**: `_get_prediction_stub()` returns a deterministic hash-based risk score (no DB beyond wide-table assembly).
- **Data sources for prediction**: Only `vitals_hourly` is required. Chemistry, procedures, coagulation, and SOFA are optional (sparse data — many patients lack rows in a given window). Missing columns are filled with NaN; the sklearn pipeline tolerates this.
- **Known issue — `latent_class` always null**: The model expects a `latent_class` input feature (likely from an upstream LCA/clustering step during training), but this value is not present in any MIMIC source table or simulation table. It is always NaN at inference time. The model still predicts (RandomForest handles NaN), but the UI shows "—" for Latent Class. To fix: determine how `latent_class` was computed during training and replicate that computation at inference time, or retrain the model without it.

### Frontend: D3.js charts (`templates/patients/show.html`)
Patient detail page uses D3.js v7 (loaded from CDN) for all clinical charts. Data is embedded as JSON in hidden `<script>` tags by the server and parsed on page load.

Four tabbed chart panels (tabs are lazy-rendered on first click):
- **Cardiovascular**: Heart rate (bpm), SBP / DBP / MBP (mmHg)
- **Respiratory**: SpO2 (%), respiratory rate (/min), temperature (°C)
- **Labs**: Glucose (mg/dL), chemistry panel (Na, HCO3, K, Ca), coagulation (INR, PTT)
- **SOFA Score**: Total SOFA bar chart (color-coded by severity) + stacked organ-component breakdown with Sepsis-3 threshold line at SOFA=2

The `buildMultiPanel()` helper supports stacked panels, dual Y-axes, normal/warning bands, and a unified crosshair tooltip. `buildSofaChart()` renders the SOFA visualization.

### Request flow
```
URL → patients/urls.py
        ├── views.py  (HTML: patient_list, patient_detail)
        ├── views.py  (Simulation JSON: advance_time, rewind_time, play, pause, reset, simulation_status)
        └── api.py    (JSON: get_static_features, get_hourly_features,
                              get_hourly_wide_features, get_feature_bundle,
                              get_prediction_view)
                         └── services.py  (raw SQL + model_local)
                                └── pipeline.py  (advance_hour / rewind_hour)
```

### Infrastructure
`terraform/` provisions an AWS RDS PostgreSQL instance for the MIMIC-IV database. The Django app runs locally or in Docker; only the database is in AWS. See `terraform/README.md` for the full data loading procedure (MIMIC-IV 3.1 is ~128 GB).

## Known Issues / Future Work

### 1. Manual view setup (TODO: automate)
**Current state:** The SQL scripts in `scripts/` (01–09) must be run manually against the database via `psql`. Django does not create or manage these views; it only queries them. After running the scripts, `python manage.py preload_cohort_cache` must also be run to populate the cache tables.

**Proposed fix:** Add an automated mechanism so the app can bootstrap its own schema, e.g.:
- Django management command (e.g. `python manage.py setup_views`) that connects to the DB and executes the scripts in order
- Or: run scripts on first startup / `migrate` (with a flag to skip if views already exist)

**Goal:** Eliminate manual `psql` steps; `docker compose up` or `runserver` should be sufficient for a working setup.

### 2. Real-time simulation mechanism (TODO)
**Current state:** The simulation supports manual step-through (+1/−1) and auto-play (background thread advancing at a configurable speed). It is wall-clock-based but not truly tied to real ICU time.

**Proposed direction:** Introduce a mechanism that simulates real-time progression more faithfully, e.g.:
- Live mode: backend advances time automatically; frontend polls or uses WebSockets for updates
- Configuration for simulation speed (e.g. `SIMULATION_SPEED=60` → 1 sim hour per 60 real seconds)

**Goal:** Support eventual live deployment patterns where `current_hour` tracks real elapsed ICU time.

## Environment Variables

Copy `.env.example` to `.env`. Key variables:

| Variable | Default | Purpose |
|---|---|---|
| `DB_NAME` | `sepsis` | Postgres database name |
| `DB_SCHEMA` | `mimiciv_derived` | Postgres search_path schema |
| `MODEL_ARTIFACTS_DIR` | `patients/model_artifacts` | Directory with `sepsis_rf_pipeline.joblib` and `feature_cols.joblib`; unset or missing = stub mode |
