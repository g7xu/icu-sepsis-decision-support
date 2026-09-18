# ICU Sepsis Decision Support

An interpretable early-warning system for adult ICU sepsis risk, demonstrated on a 51-patient MIMIC-IV cohort with an hour-by-hour simulation clock.

**Live:** https://icu-sepsis-detect.g7xu.dev

## How it works

- A Django app reads pre-computed hourly feature tables (materialized views derived from MIMIC-IV 3.1) from PostgreSQL.
- A bundled scikit-learn pipeline in `models/` scores each patient in-process. No external model service is needed.
- Similar-patient search compares the current feature vector against roughly 26,000 non-cohort ICU stays by cosine similarity.
- Hosting is Vercel (Python serverless) plus a Neon PostgreSQL database holding a pruned copy of the derived tables.

## Run locally

You need Python 3.12 and a PostgreSQL database that already contains the `fisi9t_*` tables. Either point at the production Neon database (credentials from a maintainer) or build the tables yourself from MIMIC-IV, which requires PhysioNet credentialed access. See [docs/RUNNING.md](docs/RUNNING.md) for both.

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in DB_*; leave MODEL_SERVICE_URL empty
python manage.py migrate
python manage.py runserver
```

Then open http://localhost:8000/patients/.

## Deploy

Deploys are manual from the repo root. Pushing to `main` does not deploy.

```bash
npx vercel deploy --prod
```

Configuration, environment variables, and verification steps are in [docs/RUNNING.md](docs/RUNNING.md).

## Docs

- [docs/RUNNING.md](docs/RUNNING.md) — local setup, configuration reference, prediction flow, deployment
- [docs/MIGRATION_VERCEL_NEON.md](docs/MIGRATION_VERCEL_NEON.md) — hosting history and how the pruned dataset was built
- [docs/SIMILARITY_SETUP.md](docs/SIMILARITY_SETUP.md) — building the materialized views from MIMIC-IV
- [architecture/](architecture/) — ERD and MVC flow diagrams

## Repository structure

```
.
├── config/         # Django settings and WSGI entrypoint (exposes `app` for Vercel)
├── patients/       # The Django app: views, JSON API, feature assembly, scoring, ORM
├── models/         # Serialized scikit-learn pipeline
├── templates/      # Django HTML templates
├── static/, styles/# CSS, JavaScript, images
├── scripts/        # SQL that builds the fisi9t_* materialized views from MIMIC-IV
├── docs/           # Guides
├── architecture/   # Diagrams
├── terraform/      # Retired AWS infrastructure, kept for reference
├── vercel.json     # Routes every request to config/wsgi.py
└── Dockerfile, docker-compose.yml, requirements.txt
```

## Team

- [Guoxuan Xu](https://www.linkedin.com/in/guoxuan-xu-30a572269/)
- [Varun Pabreja](https://www.linkedin.com/in/varun-pabreja/)
- [Yash Patel](https://www.linkedin.com/in/ypat353/)
- [Ethan Vo](https://www.linkedin.com/in/vo-ethan/)

## License

Source code is released under the [MIT License](LICENSE).

The application uses [MIMIC-IV](https://physionet.org/content/mimiciv/), which requires PhysioNet credentialed access. Users must have an approved PhysioNet account to work with the underlying clinical data.
