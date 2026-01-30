# icu-sepsis-decision-support

Clinician-facing sepsis risk platform backend scaffold:

- **Runtime**: Python 3.11
- **API**: FastAPI
- **DB**: PostgreSQL 14
- **Local dev**: Docker + Docker Compose

## Quickstart (local)

Bring up API + Postgres:

```bash
docker compose up --build
```

Then open:

- `http://localhost:8000/health`
- `http://localhost:8000/docs`

## Repository structure

```
.
├── app/
│   ├── main.py
│   ├── core/          # config, db session, logging
│   ├── models/        # SQLAlchemy ORM models
│   ├── schemas/       # Pydantic request/response models
│   ├── controllers/   # FastAPI routers
│   ├── engines/       # business logic, replay, ML adapters
│   └── services/      # db access layer (optional but encouraged)
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

## Environment configuration

Compose loads `.env.example` by default. For local overrides, you can copy it to `.env` and adjust values.
