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
├── scripts/           # SQL for views (run on MIMIC-IV DB)
├── templates/
├── SETUP_VIEWS.md     # View setup & env config procedure
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

The `scripts/` directory contains SQL for regular **views** (no materialized views — zero extra storage). Run them in order on your MIMIC-IV database. See [SETUP_VIEWS.md](SETUP_VIEWS.md) for the full procedure.

## Environment

Copy `.env.example` to `.env` and fill in your values. For AWS RDS, see [SETUP_VIEWS.md](SETUP_VIEWS.md) Step 2.
