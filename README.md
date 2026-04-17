# ICU Sepsis Decision Support

An interpretable early warning system for adult ICU sepsis risk. The application reads from a MIMIC-IV PostgreSQL database, runs a time-stepped ICU simulation, and serves real-time sepsis predictions using an in-process scikit-learn model.

## Getting Started

To run the app locally against a MIMIC-IV PostgreSQL instance:

1. Install the [prerequisites](#prerequisites).
2. Provision PostgreSQL and load MIMIC-IV — see [Database Setup](#database-setup).
3. Configure your `.env` — see [Environment Setup](#environment-setup).
4. Start the stack with Docker Compose — see [Run Locally](#run-locally).

### Prerequisites

- [Docker & Docker Compose](https://docs.docker.com/get-docker/)
- [MIMIC-IV access](https://physionet.org/content/mimiciv/3.1/) — requires a PhysioNet credentialed account
- PostgreSQL client (`psql`)

### Database Setup

All modes (including demo) require MIMIC-IV data in PostgreSQL. Follow the full guide:

**[Running & Setup Guide](docs/RUNNING.md)** — covers local PostgreSQL or AWS RDS provisioning, loading MIMIC-IV data, creating application views, and running migrations.

### Environment Setup

1. **Copy environment file**
   ```bash
   cp .env.example .env
   ```

2. **Edit `.env`** with your database credentials (`DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, etc.). See `.env.example` for all options.

### Run Locally

```bash
docker compose up --build
open http://localhost:8000/patients/
```

## Deployment

See the full guide: **[Running & Setup Guide](docs/RUNNING.md)**

<!-- ## Application Usage

TODO: Add screenshots and usage guide showing:
- Patient list view with simulation clock
- Patient detail view with clinical charts
- Prediction detail view with risk score timeline
-->

## Repository Structure

```
.
├── config/         # Django settings
├── patients/       # Main Django app (views, API, features, scoring, ORM)
├── models/         # ML model artifacts (joblib)
├── templates/      # Django HTML templates
├── static/         # CSS, JavaScript, images
├── scripts/        # SQL for MIMIC-IV materialized views
├── terraform/      # AWS infrastructure (RDS, EC2, ECR)
├── docs/           # Setup and deployment guides
├── architecture/   # ERD and architecture diagrams
└── docker-compose.yml, Dockerfile, deploy.sh, requirements.txt
```

To learn more about the architecture, see [architecture/](architecture/).


## Team

- [Guoxuan Xu](https://www.linkedin.com/in/guoxuan-xu-30a572269/)
- [Varun Pabreja](https://www.linkedin.com/in/varun-pabreja/)
- [Yash Patel](https://www.linkedin.com/in/ypat353/)
- [Ethan Vo](https://www.linkedin.com/in/vo-ethan/)


## License

This project's source code is released under the [MIT License](LICENSE).

It uses [MIMIC-IV](https://physionet.org/content/mimiciv/), which requires PhysioNet credentialed access. Users must have an approved PhysioNet account to access the underlying clinical data.
