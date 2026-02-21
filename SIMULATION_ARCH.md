# Simulation Architecture

## Overview

This application simulates a real-time ICU sepsis monitoring system using MIMIC-IV as the
source of truth. Rather than pre-building static materialized views of all historical data,
the system uses a **live pipeline** that replays MIMIC-IV data one simulated hour at a time
into a small set of Django-managed tables in a dedicated `simulation` schema.

---

## Data Flow

```
MIMIC-IV (RDS, read-only)
  mimiciv_icu.icustays              — patient ICU stays
  mimiciv_derived.vitalsign         — 13M rows of raw vitals
  mimiciv_icu.procedureevents       — procedure events
  mimiciv_icu.d_items               — item label lookup
  mimiciv_derived.chemistry         — chemistry labs
  mimiciv_derived.coagulation       — coagulation labs
  mimiciv_derived.sofa_hourly       — hourly SOFA scores
        │
        ▼  advance_hour() in patients/pipeline.py
simulation schema (RDS, Django-managed, writable)
  simulation.sim_patient              ← admitted patients
  simulation.sim_vitalsign_hourly     ← hourly vitals
  simulation.sim_procedureevents_hourly
  simulation.sim_chemistry_hourly
  simulation.sim_coagulation_hourly
  simulation.sim_sofa_hourly
        │
        ▼  Django app reads sim_* tables
  patient_list, patient_detail, prediction API endpoints
```

**Why this approach:**
- MIMIC-IV is the "ground truth" hospital record system (read-only)
- Simulation tables are the "live ICU state" — tiny (60 patients × 24h × 6 tables ≈ 8,640 rows max)
- `advance_hour()` is the data pipeline — it queries 60 patients' data from MIMIC, aggregates to
  hourly vectors, and writes to sim tables
- True rewind is supported: going backward deletes the corresponding sim rows, re-fetching
  from MIMIC on forward play again
- No Redis/Celery needed at this scale

---

## Cohort

The simulation covers **60 ICU patients admitted on March 13, 2025** (MIMIC-IV data).
Defined in [patients/cohort.py](patients/cohort.py) as `PATIENT_STAYS` — a list of
`(subject_id, stay_id, hadm_id)` tuples.

To change the cohort, edit `PATIENT_STAYS` in `cohort.py`. No DB changes needed — the
pipeline reads `COHORT_STAY_IDS` at runtime.

---

## Simulation Tables (simulation.* schema)

All tables are Django-managed (`managed = True`). Created by `python manage.py migrate`.

| Table | Source | Key columns |
|-------|--------|-------------|
| `sim_patient` | `mimiciv_icu.icustays` + `mimiciv_derived.age` + `icustay_detail` | subject_id, stay_id, hadm_id, intime, gender, anchor_age |
| `sim_vitalsign_hourly` | `mimiciv_derived.vitalsign` (hourly AVG) | stay_id, charttime_hour, heart_rate, sbp, dbp, … |
| `sim_procedureevents_hourly` | `mimiciv_icu.procedureevents` + `d_items` | stay_id, charttime_hour, item_label, value, … |
| `sim_chemistry_hourly` | `mimiciv_derived.chemistry` | stay_id, charttime_hour, bicarbonate, calcium, sodium, potassium |
| `sim_coagulation_hourly` | `mimiciv_derived.coagulation` | stay_id, charttime_hour, inr, pt, ptt, d_dimer, … |
| `sim_sofa_hourly` | `mimiciv_derived.sofa_hourly` | stay_id, charttime_hour, sofa_24hours, respiration, … |

All hourly tables are indexed on `(stay_id, charttime_hour)`.

---

## Simulation Clock Controls

The simulation clock is an in-memory dict in `patients/views.py` (`_simulation`).
It resets when the Django server restarts (by design — this is a demo tool).

### API Endpoints

| Method | URL | Description |
|--------|-----|-------------|
| `POST` | `/patients/advance-time/` | Step forward 1 hour (fetch + insert 1 hour of MIMIC data) |
| `POST` | `/patients/rewind-time/` | Step backward 1 hour (delete that hour's sim rows) |
| `POST` | `/patients/play/` | Start auto-advance loop (background thread) |
| `POST` | `/patients/pause/` | Stop auto-advance loop |
| `POST` | `/patients/reset/` | Stop clock, delete all sim rows, reset to hour -1 |
| `GET` | `/patients/simulation-status/` | Poll: `{current_hour, auto_play, speed_seconds, direction}` |

### Play Parameters

`POST /patients/play/` accepts:
- `speed_seconds` (float, default 5.0) — real seconds per simulated hour
  - 1× = `speed_seconds=5.0`
  - 2× = `speed_seconds=2.5`
  - 0.5× = `speed_seconds=10.0`
- `direction` — `forward` (default) or `backward`

### True Rewind

When rewinding, the pipeline **deletes** all sim rows for the removed hour:
```
sim_vitalsign_hourly WHERE charttime_hour = '2025-03-13 HH:00:00'
sim_procedureevents_hourly WHERE charttime_hour = ...
sim_chemistry_hourly WHERE charttime_hour = ...
sim_coagulation_hourly WHERE charttime_hour = ...
sim_sofa_hourly WHERE charttime_hour = ...
sim_patient WHERE intime__hour = HH (patients admitted at that hour)
```
When the simulation plays forward again, the pipeline re-fetches from MIMIC source tables.

---

## Pipeline Implementation (`patients/pipeline.py`)

```
advance_hour(current_hour)
  ├── _fetch_new_admissions(current_hour)
  │     → SELECT from mimiciv_icu.icustays WHERE stay_id IN (cohort) AND EXTRACT(HOUR FROM intime) = H
  │     → SimPatient.bulk_create(..., ignore_conflicts=True)
  │
  ├── _fetch_vitals_for_hour(stay_ids, hour_start, hour_end)
  │     → SELECT AVG(...) FROM mimiciv_derived.vitalsign WHERE stay_id=ANY(...) AND charttime IN [H, H+1)
  │     → GROUP BY stay_id, DATE_TRUNC('hour', charttime)
  │     → SimVitalsignHourly.bulk_create(...)
  │
  ├── _fetch_procedures_for_hour(stay_ids, hour_start, hour_end)
  │     → SELECT FROM mimiciv_icu.procedureevents JOIN d_items WHERE stay_id=ANY(...)
  │     → SimProcedureeventsHourly.bulk_create(...)
  │
  ├── _fetch_chemistry_for_hour(stay_ids, hour_start, hour_end)
  │     → SELECT MIN/AVG/MAX FROM mimiciv_derived.chemistry JOIN sim_patient
  │     → SimChemistryHourly.bulk_create(...)
  │
  ├── _fetch_coagulation_for_hour(stay_ids, hour_start, hour_end)
  │     → SELECT FROM mimiciv_derived.coagulation JOIN sim_patient
  │     → SimCoagulationHourly.bulk_create(...)
  │
  └── _fetch_sofa_for_hour(stay_ids, current_hour)
        → SELECT FROM mimiciv_derived.sofa_hourly WHERE stay_id=ANY(...) AND hr = H
        → SimSofaHourly.bulk_create(...) [gracefully skips if sofa_hourly doesn't exist]
```

**Note on chemistry/coagulation join:** The MIMIC derived tables `chemistry` and `coagulation`
are keyed by `subject_id` (not `stay_id`). The pipeline joins through `sim_patient` to resolve
`subject_id → stay_id` for cohort patients.

---

## Dynamic Table Resolution (`patients/services.py`)

`DERIVED_TABLE_CANDIDATES` lists candidate table names in preference order for each feature source.
`_pick_first_existing()` probes Postgres at runtime using `to_regclass()` and returns the first match.

After migration, simulation tables are prepended, so they take priority:
```
profile:            simulation.sim_patient → fisi9t_unique_patient_profile → ...
vitals_hourly:      simulation.sim_vitalsign_hourly → fisi9t_vitalsign_hourly → ...
procedures_hourly:  simulation.sim_procedureevents_hourly → fisi9t_procedureevents_hourly → ...
chemistry_hourly:   simulation.sim_chemistry_hourly → fisi9t_chemistry_hourly → ...
coagulation_hourly: simulation.sim_coagulation_hourly → fisi9t_coagulation_hourly → ...
sofa_hourly:        simulation.sim_sofa_hourly → sofa_hourly → mimiciv_derived.sofa_hourly → ...
```

---

## Setup

### One-time setup (first time)
```bash
# 1. Start the app
docker compose up --build

# 2. Run migrations (creates simulation schema + tables)
docker exec icu-sepsis-decision-support-web-1 python manage.py migrate

# 3. Open the UI and start the simulation
open http://localhost:8000/patients/
```

`run_setup_views.sh` is **no longer required** for the app to run.
The fisi9t_* materialized views (scripts 05–09) are archived for reference but the pipeline
queries MIMIC source tables directly.

### Running the simulation
1. Click **Reset** to clear any stale data
2. Click **Play ▶** to start auto-advancing at 5s/hour (default)
3. Adjust **Speed** dropdown to change pace
4. Use **+1 / -1** for manual stepping
5. Click **Rewind ◀** to go backward (deletes that hour's data)
6. Click **Pause ⏸** to stop auto-play

---

## EC2 Production Deployment (future)

### RDS Schema Layout
```
DB: mimiciv
  mimiciv_icu.*       — MIMIC-IV raw ICU (read-only)
  mimiciv_derived.*   — MIMIC-IV derived concepts (read-only)
  simulation.*        — Django sim tables (writable, Django-managed)
  public.*            — Django auth/sessions
```

### Migration Path to EC2

| Phase | What | Notes |
|-------|------|-------|
| 1 (now) | Local Docker + RDS | `docker compose up`, Django on localhost |
| 2 | EC2 t3.medium in same VPC as RDS | `docker compose up -d`, open sg 80/443, Elastic IP |
| 3 | ALB + HTTPS | ACM cert, ALB in front, DNS A record |
| 4 | Persistent sim state (optional) | Store `current_hour` in `simulation.sim_state` table |
| 5 | Auto Scaling + RDS Multi-AZ | For production resilience |

### Key ENV vars for EC2
```bash
DB_HOST=<rds-endpoint>
DB_NAME=mimiciv
DB_USER=<user>
DB_PASSWORD=<password>
DB_SCHEMA=mimiciv_derived
SECRET_KEY=<generate-new-one>
ALLOWED_HOSTS=<ec2-public-ip>,<your-domain>
MODEL_SERVICE_URL=<ec2-model-endpoint>
```
