# ICU Sepsis Decision Support

An interpretable early warning system for adult ICU sepsis risk. The application reads from a MIMIC-IV PostgreSQL database, runs a time-stepped ICU simulation, and serves real-time sepsis predictions using an in-process scikit-learn model.

## Getting Started

### Prerequisites

- [Docker & Docker Compose](https://docs.docker.com/get-docker/)
- [MIMIC-IV access](https://physionet.org/content/mimiciv/3.1/) — requires a PhysioNet credentialed account
- PostgreSQL client (`psql`)

### Database Setup

All modes (including demo) require MIMIC-IV data in PostgreSQL. Follow the full guide:

**[Database Setup Guide](docs/database-setup.md)** — covers local PostgreSQL or AWS RDS provisioning, loading MIMIC-IV data, creating application views, and running migrations.

### Environment Setup

1. **Copy environment file**
   ```bash
   cp .env.example .env
   ```

2. **Edit `.env`** with your database credentials (`DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, etc.). See `.env.example` for all options.

### Run Locally

```bash
docker compose up --build
open http://localhost:8000/demo/patients/
```

## Deployment

Deploy to AWS using Terraform and the provided deploy script. See the full guide:

**[Deployment Guide](docs/deployment.md)**

<!-- ## Application Usage

TODO: Add screenshots and usage guide showing:
- Patient list view with simulation clock
- Patient detail view with clinical charts
- Prediction detail view with risk score timeline
-->

## Repository Structure

```
.
├── config/              # Django settings
├── patients/            # Main Django app
│   ├── views.py         #   HTML views + simulation clock controls
│   ├── api.py           #   JSON API endpoints
│   ├── services.py      #   Business logic (raw SQL, predictions)
│   ├── pipeline.py      #   Simulation engine (advance/rewind hour)
│   ├── models.py        #   Django ORM models
│   └── model_artifacts/ #   ML model files (joblib)
├── templates/           # Django HTML templates
├── static/              # CSS, JavaScript, images
├── scripts/
│   ├── views/           #   SQL scripts for application views
│   └── buildmimic/      #   MIMIC-IV data loading scripts (from MIT-LCP/mimic-code)
├── terraform/           # AWS infrastructure (RDS, EC2, ECR)
├── docs/                # Setup and deployment guides
├── deploy.sh            # Automated AWS deployment
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

To learn more about the architecture, see [PLACEHOLDER].


## Team

- [Guoxuan Xu](https://www.linkedin.com/in/guoxuan-xu-30a572269/)
- [Varun Pabreja](https://www.linkedin.com/in/varun-pabreja/)
- [Yash Patel](https://www.linkedin.com/in/ypat353/)
- [Ethan Vo](https://www.linkedin.com/in/vo-ethan/)


## License

This project uses [MIMIC-IV](https://physionet.org/content/mimiciv/), which requires PhysioNet credentialed access. Users must have an approved PhysioNet account to access the underlying clinical data.
