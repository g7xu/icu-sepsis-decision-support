# Migration: AWS (EC2 + RDS) → Vercel + Neon

**Date:** 2026-08-01
**Result:** ~$50/month AWS bill → ~$1.90/month (one retained snapshot), then $0 after final cleanup.

## Architecture

| | Before (AWS) | After |
|---|---|---|
| Web app | EC2 `t3.small` + nginx + Docker (ECR) | Vercel serverless (`@vercel/python`, Hobby plan, free) |
| Database | RDS PostgreSQL 15 `db.t4g.micro`, 20GB (1.9GB used) | Neon Postgres 17, free tier (~25MB used of 512MB) |
| ML model | Separate EC2 model service (`POST /predict`) | In-process: bundled `models/sepsis_model.joblib`, used automatically when `MODEL_SERVICE_URL` is unset ([patients/scoring.py](../patients/scoring.py), [patients/local_model.py](../patients/local_model.py)) |
| Domain | `icu-sepsis-detect.g7xu.dev` → Cloudflare (proxied) → EC2 | Cloudflare CNAME (**DNS only**) → `cname.vercel-dns.com` → Vercel |
| Static files | collectstatic + nginx | WhiteNoise via Django finders (`WHITENOISE_USE_FINDERS` when `VERCEL=1`; no collectstatic step) |

**Live URLs:**
- https://icu-sepsis-detect.g7xu.dev (custom domain)
- https://icu-sepsis-decision-support.vercel.app (Vercel default)

## Neon database

- **Project:** `icu-sepsis` (`blue-mountain-22233537`), personal org `org-tiny-art-88342940`, region `aws-us-east-1`
- **Credentials:** in `.env` (old RDS values preserved in `.env.rds.backup`, both gitignored)
- **SSL:** required — `DB_SSLMODE` defaults to `require` in [config/settings.py](../config/settings.py); set `DB_SSLMODE=prefer` only for a bare local Postgres without SSL

### Pruned dataset (1.9GB → ~25MB)

The app never reads raw MIMIC-IV. It reads eight tables in schema `mimiciv_derived`, and the copy in Neon is pruned to exactly what the code paths touch:

| Table | Contents in Neon | Rows |
|---|---|---|
| `fisi9t_unique_patient_profile` | full | 26,105 |
| `sepsis3` | full (similarity outcome labels) | 41,296 |
| `fisi9t_vitalsign_hourly` | cohort (51 patients) only | 1,165 |
| `fisi9t_chemistry_hourly` | cohort only | 1,165 |
| `fisi9t_coagulation_hourly` | cohort only | 1,165 |
| `fisi9t_sofa_hourly` | cohort only | 1,062 |
| `fisi9t_procedureevents_hourly` | cohort only | 1,201 |
| `fisi9t_feature_matrix_hourly` | cohort full history **+ latest-hour row per non-cohort patient** (similarity candidate pool) | 27,168 |

Why this is safe: non-cohort patients are only ever touched by the similarity search
(`_fetch_candidate_rows` in [patients/scoring.py](../patients/scoring.py) takes the latest hour
per patient) and profile enrichment. Hourly charts are only rendered for the 51-patient cohort.

**⚠ If the cohort in [patients/cohort.py](../patients/cohort.py) ever changes**, the new
patients' full hourly rows must be re-exported from a MIMIC-IV source (retained RDS snapshot,
or a rebuild from PhysioNet using `scripts/01–11_*.sql`) and loaded into Neon.

## Vercel deployment

- **Project:** `icu-sepsis-decision-support` (`prj_6VT5lNM52B2fkpIxvsFRE463IKhu`)
- **Config:** [vercel.json](../vercel.json) routes everything to `config/wsgi.py` (which exposes `app` for the builder); [.vercelignore](../.vercelignore) keeps terraform/docs/etc. out of the bundle
- **Deploy:** `npx vercel deploy --prod` from the repo root
- **Env vars** (production): `DB_*`, `DB_SSLMODE=require`, `SECRET_KEY`, `DEBUG=False`, `ALLOWED_HOSTS=.vercel.app,icu-sepsis-detect.g7xu.dev`, `CSRF_TRUSTED_ORIGINS=https://icu-sepsis-decision-support.vercel.app,https://icu-sepsis-detect.g7xu.dev`, `MODEL_HISTORY_HOURS=6`, `LOG_LEVEL=INFO`. **`MODEL_SERVICE_URL` intentionally unset** → in-process model.
- Dependency bundle (numpy/pandas/scipy/sklearn) fits within Vercel's function size limit — no trimming was needed.

### Security notes (from post-deploy review)

- CSRF trusted origins are **never** derived as wildcards from leading-dot hosts
  (`.vercel.app` would otherwise trust every site on the platform). On shared PaaS domains,
  `CSRF_TRUSTED_ORIGINS` must be set explicitly; POSTs fail closed without it.
- DB connections default to `sslmode=require`.
- Verified: POST from the real origin → 200; forged POST from a foreign `*.vercel.app` origin → 403.

## Verification performed (2026-08-01)

Local against Neon, then production on both domains:
patient list/detail, hourly features, prediction (risk score identical to the RDS-backed run:
`0.3718…` for patient 13145844 @ 2168-02-20T10:00), similar-patients over the 26k candidate
pool, static assets, simulation-clock POST with CSRF, clean runtime error logs.

## AWS decommission status

Done 2026-08-01 (`terraform destroy`, 20 resources): EC2 instance + EIP, RDS instance +
subnet group, security groups + rules, IAM role/profile, key pair, and the `icu-sepsis-team`
ECR repo (force-deleted separately).

### Stray-resource cleanup — done 2026-08-02

Three resources created outside Terraform (so the destroy missed them) were removed:

| Resource | Was costing |
|---|---|
| Unattached EIP `34.202.24.46` (orphan from an older deploy) | ~$3.65/mo |
| Old 150GB manual RDS snapshot `icu-sepsis-db-snapshot` (2026-04-17, pre-slimming full DB) | ~$14/mo |
| Old ECR repo `icu-sepsis` (1 image, 279MB) | ~$0.03/mo |

Post-cleanup sweep confirmed: no EC2/RDS instances, no EIPs, no EBS volumes, no ECR repos.

### Retained on purpose

- **`icu-sepsis-final-pre-decommission`** — 20GB RDS snapshot of the final database state,
  taken 2026-08-01 (~$1.90/mo). Safety net; delete once the new setup has proven itself:
  `aws rds delete-db-snapshot --region us-east-1 --db-snapshot-identifier icu-sepsis-final-pre-decommission`

### Restore path (if ever needed)

1. Restore the snapshot: `aws rds restore-db-instance-from-db-snapshot --db-instance-identifier icu-sepsis-restore --db-snapshot-identifier icu-sepsis-final-pre-decommission --db-instance-class db.t4g.micro`
2. Or rebuild from scratch: load MIMIC-IV 3.1 from PhysioNet, run `scripts/01–11_*.sql` (see [terraform/README.md](../terraform/README.md), 4–8 hours).
3. Re-export the pruned Neon dataset the same way it was built: schema dump of the 8 tables + full copies of profile/sepsis3 + cohort-filtered copies of the hourly tables + cohort-full-plus-latest-per-non-cohort copy of the feature matrix.
