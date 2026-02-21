# icu-sepsis-decision-support

An interpretable early warning system for Adult ICU sepsis risk, focusing on trend analysis and 6-hour prediction windows.

- **Runtime**: Python 3.11
- **Framework**: Django
- **DB**: PostgreSQL 14
- **Local dev**: Docker + Docker Compose

## Quickstart

```bash
docker compose up --build
```

Then open `http://localhost:8000/patients/`. See [RUNNING.md](RUNNING.md) for detailed run instructions and model service setup.

## Repository structure

```
.
├── config/            # Django settings
├── patients/          # Patient app (views, API, services)
├── scripts/           # Reference SQL for materialized views
├── templates/
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── RUNNING.md         # Run instructions, model service contract
```

## API Endpoints

**Patient Features (ML model input)**
- `GET /patients/<ids>/features/static` - Demographics
- `GET /patients/<ids>/features/hourly` - Raw hourly streams (vitals, procedures, SOFA)
- `GET /patients/<ids>/features/hourly-wide` - Merged wide table for ML (1 row/hour)
- `GET /patients/<ids>/feature-bundle` - Combined static + hourly

**Prediction**
- `GET /patients/<ids>/prediction?as_of=<ISO datetime>&window_hours=24` - Risk score + comorbidity group

## SQL & Data Sources

The `scripts/` directory contains reference SQL for materialized views (e.g. `fisi9t_vitalsign_hourly`). These views must exist in your Postgres database for the feature endpoints to return data.

## Environment

Compose loads `.env.example` by default. Copy to `.env` for local overrides.
