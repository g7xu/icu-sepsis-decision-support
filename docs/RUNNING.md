# Running and Deploying

## Run locally

Requirements: Python 3.12 and a PostgreSQL database that already has the `fisi9t_*` materialized views and `sepsis3`. Two ways to get one:

- **Use the production Neon database.** Ask a maintainer for the connection values and put them in `.env`. This is the normal path.
- **Build from MIMIC-IV.** Load MIMIC-IV 3.1 into PostgreSQL (PhysioNet credentialed access required), then run `scripts/01_*.sql` through `scripts/11_*.sql` in order. This takes several hours. [SIMILARITY_SETUP.md](SIMILARITY_SETUP.md) walks through the view scripts.

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # edit DB_*
python manage.py migrate      # creates django_session and the two cache tables
python manage.py runserver
open http://localhost:8000/patients/
```

Docker alternative: `docker compose up --build`. The compose file runs only the web container and reads `.env`, so the database must already exist somewhere reachable.

## Configuration reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `DB_NAME` | yes | — | Database name |
| `DB_USER` | yes | — | Database user |
| `DB_PASSWORD` | yes | — | Database password |
| `DB_HOST` | yes | `localhost` | Database host |
| `DB_PORT` | no | `5432` | Database port |
| `DB_SCHEMA` | no | `mimiciv_derived` | Schema prepended to `search_path`; holds the `fisi9t_*` tables |
| `DB_SSLMODE` | no | `require` | libpq `sslmode`. Neon needs `require`; a local Postgres without TLS needs `prefer` |
| `SECRET_KEY` | yes | — | Django secret key |
| `DEBUG` | no | `True` | Must be `False` in production |
| `ALLOWED_HOSTS` | no | `localhost,127.0.0.1` | Comma-separated hostnames; a leading dot is a wildcard |
| `CSRF_TRUSTED_ORIGINS` | prod | derived | Comma-separated `https://` origins. Derived from `ALLOWED_HOSTS` when `DEBUG=False`, but never from leading-dot hosts, so set it explicitly on Vercel |
| `MODEL_SERVICE_URL` | no | empty | External prediction API. Empty means score in-process with the bundled model |
| `MODEL_SERVICE_TIMEOUT` | no | `30` | Seconds; only with `MODEL_SERVICE_URL` |
| `MODEL_SERVICE_API_KEY` | no | empty | Bearer token; only with `MODEL_SERVICE_URL` |
| `MODEL_HISTORY_HOURS` | no | `6` | Hours of prior feature rows in each model payload |
| `LOCAL_MODEL_PATH` | no | `models/sepsis_model.joblib` | Path to the serialized scikit-learn pipeline |
| `SIMILARITY_CSV_PATH` | no | `static/similarity_matrix.csv` | Output path for `manage.py export_similarity_matrix` |

## How a prediction is produced

`GET /patients/<subject_id>/<stay_id>/<hadm_id>/prediction?as_of=<ISO datetime>`

1. If a `PredictionResult` row exists for this patient and `as_of`, it is returned. The same `(patient, as_of)` always yields the same score.
2. Otherwise the app reads `fisi9t_feature_matrix_hourly` (one wide row per hour), picks the latest row at or before `as_of`, and gathers `MODEL_HISTORY_HOURS` of earlier rows. If the feature matrix is absent it falls back to intersecting the five per-domain hourly tables on `(subject_id, stay_id, charttime_hour)`.
3. The payload is scored by the bundled pipeline in `patients/local_model.py`, or POSTed to `<MODEL_SERVICE_URL>/predict` when that is configured. A failed external call also falls back to the local model.
4. The first `comorbidity_group` written for a patient is reused for all later hours so the UI does not flicker. A model that returns none gets `"unknown"`.
5. The result is stored in `PredictionResult`. Similar-patient results are cached the same way in `SimilarPatientsResult`.

To clear both caches:

```bash
python manage.py shell -c "from patients.models import PredictionResult, SimilarPatientsResult; PredictionResult.objects.all().delete(); SimilarPatientsResult.objects.all().delete()"
```

### The bundled model

`models/sepsis_model.joblib` is a scikit-learn `Pipeline` serialized with scikit-learn 1.8.0. `requirements.txt` pins that version exactly. Loading it under another version prints `InconsistentVersionWarning` and may change scores, so bump scikit-learn only together with a re-exported artifact. `joblib.load` runs arbitrary code from the file; never point `LOCAL_MODEL_PATH` at an untrusted artifact.

### Optional external model service

Not used in production. If you run one, it must accept:

`POST /predict`

```json
{
  "patient": {"subject_id": 123, "stay_id": 456, "hadm_id": 789},
  "as_of": "2168-02-20T10:00:00",
  "current_feature_vector": {"...": "one wide feature row"},
  "source_keys": {"...": "(subject_id, stay_id, charttime_hour) per source table"},
  "history_feature_vectors": [{"...": "..."}]
}
```

and respond with:

```json
{"risk_score": 0.42, "comorbidity_group": "cardiovascular"}
```

`comorbidity_group` may be omitted after the first call for a patient.

## Endpoints

All under `/patients/<subject_id>/<stay_id>/<hadm_id>/`. The examples use a cohort patient that exists in the production database.

```bash
BASE=http://localhost:8000/patients/13145844/31142781/21653707
curl "$BASE/features/static"
curl "$BASE/features/hourly?as_of=2168-02-20T10:00:00&window_hours=24"
curl "$BASE/features/hourly-wide?as_of=2168-02-20T10:00:00&window_hours=24"
curl "$BASE/feature-bundle?as_of=2168-02-20T10:00:00"
curl "$BASE/prediction?as_of=2168-02-20T10:00:00"
curl "$BASE/similar-patients?as_of=2168-02-20T10:00:00"
```

The prediction call above returns `risk_score` 0.3718 against the production data; a different value after a dependency change means the model artifact and library versions have drifted.

## Deploy to Vercel

The Vercel project is not connected to GitHub. Deploys are manual:

```bash
npx vercel deploy --prod
```

What the deploy uses:

- `vercel.json` routes every request to `config/wsgi.py`, which exposes the WSGI application as `app`.
- `.python-version` selects Python 3.12; `requirements.txt` is installed fresh on every build.
- `.vercelignore` excludes `terraform/`, `docs/`, `scripts/`, and local files from the bundle.
- Static files are served by WhiteNoise from the source directories; there is no collectstatic step.

Production environment variables to set in the Vercel project: all `DB_*` values for Neon with `DB_SSLMODE=require`, `SECRET_KEY`, `DEBUG=False`, `ALLOWED_HOSTS=.vercel.app,icu-sepsis-detect.g7xu.dev`, `CSRF_TRUSTED_ORIGINS=https://icu-sepsis-decision-support.vercel.app,https://icu-sepsis-detect.g7xu.dev`, `MODEL_HISTORY_HOURS=6`. Leave `MODEL_SERVICE_URL` unset.

After deploying, check:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://icu-sepsis-detect.g7xu.dev/patients/
curl -s "https://icu-sepsis-detect.g7xu.dev/patients/13145844/31142781/21653707/prediction?as_of=2168-02-20T10:00:00"
```

Both domains, the custom one and `icu-sepsis-decision-support.vercel.app`, point at the same deployment. The custom domain is a Cloudflare CNAME in DNS-only mode.

## Operational notes

- Session rows accumulate in `django_session` on Neon. Run `python manage.py clearsessions` against production occasionally.
- The Neon dataset contains full hourly history only for the 51 cohort patients. If `patients/cohort.py` changes, the new patients' rows must be exported from a full MIMIC-IV build and loaded into Neon. See [MIGRATION_VERCEL_NEON.md](MIGRATION_VERCEL_NEON.md).
