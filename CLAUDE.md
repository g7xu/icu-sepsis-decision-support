# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ICU Sepsis Decision Support — a Django web app giving clinicians interpretable ML-based sepsis risk predictions and similar-patient comparisons for a cohort of 51 curated MIMIC-IV ICU patients, driven by a simulation clock that steps through each stay hour by hour.

Production runs on Vercel (Python serverless) with a Neon PostgreSQL database. Predictions are computed in-process from a bundled scikit-learn pipeline. There is no separate model service, no S3, and no AWS.

## Commands

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # set DB_*; MODEL_SERVICE_URL stays empty
python manage.py migrate          # creates django_session + the two cache tables
python manage.py runserver        # http://localhost:8000/patients/
python manage.py check
```

Docker alternative: `docker compose up --build` runs the web container against whatever `.env` points at. There is no bundled database container.

Deploy (manual; not Git-connected): `npx vercel deploy --prod` from the repo root.

Management command: `python manage.py export_similarity_matrix` writes the non-cohort feature matrix CSV (only needed when rebuilding from a full MIMIC-IV database).

There is no test suite.

## Architecture

### Django app: `patients/`

- **`cohort.py`** — `PATIENT_STAYS` (the 51 demo patients) and `get_cohort_filter()`. The boundary between "demo cohort" and the full population.
- **`models.py`** — Two kinds of models. Unmanaged read-only models mapped onto the `fisi9t_*` materialized views (`UniquePatientProfile`, `VitalsignHourly`, `ChemistryHourly`, `CoagulationHourly`, `SofaHourly`, `ProcedureeventsHourly`), and two managed cache tables (`PredictionResult`, `SimilarPatientsResult`) created by the app's single migration.
- **`db_utils.py`** — raw-SQL helpers and `DERIVED_TABLE_CANDIDATES`, which lets each table resolve with or without the schema prefix.
- **`features.py`** — assembles static and hourly feature rows for the API.
- **`scoring.py`** — `get_prediction()` (the prediction pipeline and its Postgres cache) and `get_similar_patients()` (cosine similarity over the latest-hour row of every non-cohort patient in `fisi9t_feature_matrix_hourly`).
- **`local_model.py`** — loads `models/sepsis_model.joblib` once per process and scores a payload. `joblib.load` executes arbitrary code, so only trusted artifacts may live in `models/`.
- **`api.py`** — JSON endpoints under `/patients/<subject>/<stay>/<hadm>/`: `features/static`, `features/hourly`, `features/hourly-wide`, `feature-bundle`, `prediction`, `similar-patients`.
- **`views.py`** — HTML views (list, detail, prediction) and the simulation clock, which lives in the Django session. `session_utils.py` caches per-hour results in the session as well.
- **`services.py`** — a re-export shim. `api.py` and `views.py` import `get_prediction` and friends from here; the implementations live in `scoring.py` and `features.py`.

### Prediction flow

1. Simulation clock advances (session hour increments) and the frontend calls the `prediction` endpoint with `as_of`.
2. `scoring.get_prediction()` returns the cached `PredictionResult` for `(patient, as_of)` if one exists.
3. Otherwise it reads `fisi9t_feature_matrix_hourly` (one wide row per hour), takes the latest row at or before `as_of` plus `MODEL_HISTORY_HOURS` of history, and builds the payload.
4. If `MODEL_SERVICE_URL` is set it POSTs to `<url>/predict`; if unset, or the call fails, `local_model.predict_locally()` scores in-process.
5. The first `comorbidity_group` ever written for a patient is sticky for later hours. The result is written to `PredictionResult`.

### Database

All derived tables carry the `fisi9t_` prefix and live in `DB_SCHEMA` (normally `mimiciv_derived`), plus `sepsis3` for similarity outcome labels. The Neon copy is pruned: full hourly history for the 51 cohort patients only, plus the latest-hour row per non-cohort patient. Hourly charts therefore only work for cohort patients. **Changing the cohort requires re-exporting those patients' full hourly rows from a MIMIC-IV source** — see `docs/MIGRATION_VERCEL_NEON.md`.

### Settings (`config/settings.py`)

- Session backend is the database. `SESSION_EXPIRE_AT_BROWSER_CLOSE` is on, but rows are only purged by `manage.py clearsessions`.
- `DB_SSLMODE` defaults to `require` (Neon needs TLS).
- With `DEBUG=False`, `CSRF_TRUSTED_ORIGINS` is derived from `ALLOWED_HOSTS` **except** leading-dot hosts such as `.vercel.app`, which would trust every site on the platform. Production therefore sets `CSRF_TRUSTED_ORIGINS` explicitly.
- On Vercel (`VERCEL=1`) WhiteNoise serves static files straight from the finders; there is no collectstatic step.

### Deployment (`vercel.json`, `.vercelignore`, `.python-version`)

`vercel.json` uses the legacy `builds` config to route every path to `config/wsgi.py`, which exposes the WSGI callable as `app`. `.vercelignore` keeps terraform, docs, and scripts out of the bundle. `.python-version` pins 3.12. `requirements.txt` is pinned exactly because the model artifact is tied to scikit-learn 1.8.0 and Vercel resolves dependencies on every deploy.

`terraform/` describes the retired AWS stack and is not used.

## Environment Variables

| Variable | Purpose |
|---|---|
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | PostgreSQL connection |
| `DB_SCHEMA` | Schema containing the `fisi9t_*` tables (`mimiciv_derived`) |
| `DB_SSLMODE` | libpq sslmode; default `require` |
| `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` | Django |
| `MODEL_SERVICE_URL` | Optional external `/predict` endpoint; empty means in-process model |
| `MODEL_SERVICE_TIMEOUT`, `MODEL_SERVICE_API_KEY` | Only used with `MODEL_SERVICE_URL` |
| `MODEL_HISTORY_HOURS` | Hours of feature history in each payload (default 6) |
| `LOCAL_MODEL_PATH` | Override for the joblib artifact path |
| `SIMILARITY_CSV_PATH` | Output path for `export_similarity_matrix` |

`docs/RUNNING.md` has the full reference table with defaults.

## Docs

- `docs/RUNNING.md` — local setup, configuration reference, prediction flow, deployment and verification
- `docs/MIGRATION_VERCEL_NEON.md` — how the current hosting and pruned dataset came to be
- `docs/SIMILARITY_SETUP.md` — building materialized views from a full MIMIC-IV load
- `scripts/01_*.sql` … `11_*.sql` — the view definitions, run in order against MIMIC-IV 3.1
