# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Django web application for ICU sepsis early warning. It reads from a MIMIC-IV PostgreSQL database (materialized views), runs a time-stepped ICU simulation, serves ML feature bundles via JSON API, and calls an optional external EC2 prediction service.

**Stack**: Python 3.11 · Django 4.2 · PostgreSQL 14 · Docker Compose · boto3 (S3) · httpx (EC2 model service) · D3.js v7 (frontend charts)

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
# Stub prediction (no MODEL_SERVICE_URL required)
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

### Prediction pipeline (two modes)
Controlled by `MODEL_SERVICE_URL` in `.env`:

**Stub mode** (`MODEL_SERVICE_URL` empty): `_get_prediction_stub()` in `services.py` returns a deterministic hash-based risk score. No DB or network calls beyond the wide-table assembly.

**Live mode** (`MODEL_SERVICE_URL` set): `get_prediction()` in `services.py` executes this sequence:
1. Fetch all five required hourly source tables from Postgres for the patient/time window
2. Intersect `charttime_hour` values across all five sources; select the latest common hour ≤ `as_of`
3. If `MODEL_S3_BUCKET` is set: write the current feature vector to `s3://<bucket>/<prefix>/patients/<ids>/features/<hour>.json`, load prior vectors for history
4. POST to `<MODEL_SERVICE_URL>/predict` with the feature bundle (see RUNNING.md for full contract)
5. Persist prediction + IO audit to S3 under `.../predictions/` and `.../io/`
6. For subsequent calls, reuse the first stored `comorbidity_group` from S3

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
                         └── services.py  (raw SQL + S3 + EC2 calls)
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
| `MODEL_SERVICE_URL` | `` (empty) | EC2 model endpoint; empty = stub mode |
| `MODEL_S3_BUCKET` | `` (empty) | S3 bucket for feature/prediction persistence |
| `MODEL_HISTORY_HOURS` | `6` | Hours of history vectors sent to model |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | `` | Required for S3 in live mode |
