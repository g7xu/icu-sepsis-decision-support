"""
In-memory cache singleton for demo mode.

Loads all sim_cache_* data into Python dicts at startup (~5-6 MB).
Demo views query this cache instead of hitting the database, so each
visitor's per-session clock is completely independent.
"""

import logging
from datetime import datetime

from django.db import connection

from .utils import SIM_YEAR, SIM_MONTH, SIM_DAY

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level cache — populated once by load()
# ---------------------------------------------------------------------------

_loaded = False

# {hour_int: [patient_dict, ...]}
patients_by_hour: dict[int, list[dict]] = {}

# {stay_id: patient_dict}
patients_by_stay: dict[int, dict] = {}

# {(stay_id, hour_int): [row_dict, ...]}
vitals: dict[tuple[int, int], list[dict]] = {}
procedures: dict[tuple[int, int], list[dict]] = {}
chemistry: dict[tuple[int, int], list[dict]] = {}
coagulation: dict[tuple[int, int], list[dict]] = {}
sofa: dict[tuple[int, int], list[dict]] = {}

# {(stay_id, hour_int): {ok, risk_score, latent_class}}
predictions: dict[tuple[int, int], dict] = {}


def load():
    """Load all sim_cache_* tables into memory. Idempotent."""
    global _loaded
    if _loaded:
        return

    logger.info("[demo_cache] Loading sim_cache_* tables into memory...")

    _load_patients()
    _load_hourly('simulation.sim_cache_vitalsign_hourly', vitals, [
        'subject_id', 'stay_id', 'charttime_hour',
        'heart_rate', 'sbp', 'dbp', 'mbp', 'sbp_ni', 'dbp_ni', 'mbp_ni',
        'resp_rate', 'temperature', 'temperature_site', 'spo2', 'glucose',
    ])
    _load_hourly('simulation.sim_cache_procedures', procedures, [
        'subject_id', 'stay_id', 'charttime_hour', 'charttime',
        'caregiver_id', 'itemid', 'item_label', 'item_unitname',
        'item_lownormalvalue', 'item_highnormalvalue',
        'value', 'valueuom', 'location', 'locationcategory',
        'orderid', 'linkorderid', 'ordercategoryname',
        'ordercategorydescription', 'patientweight',
        'isopenbag', 'continueinnextdept', 'statusdescription',
        'originalamount', 'originalrate',
    ])
    _load_hourly('simulation.sim_cache_chemistry_hourly', chemistry, [
        'subject_id', 'stay_id', 'charttime_hour',
        'bicarbonate', 'calcium', 'sodium', 'potassium',
    ])
    _load_hourly('simulation.sim_cache_coagulation_hourly', coagulation, [
        'subject_id', 'stay_id', 'charttime_hour',
        'd_dimer', 'fibrinogen', 'thrombin', 'inr', 'pt', 'ptt',
    ])
    _load_hourly('simulation.sim_cache_sofa_hourly', sofa, [
        'subject_id', 'stay_id', 'charttime_hour',
        'sofa_24hours', 'respiration', 'coagulation', 'liver',
        'cardiovascular', 'cns', 'renal',
    ])

    _precompute_predictions()

    _loaded = True
    total_patients = len(patients_by_stay)
    logger.info("[demo_cache] Loaded %d patients into memory", total_patients)


# ---------------------------------------------------------------------------
# Query helpers (called by demo_views.py)
# ---------------------------------------------------------------------------

def get_patients_admitted_up_to(hour: int) -> list[dict]:
    """Return all patients admitted at hours 0..hour (inclusive)."""
    result = []
    for h in range(0, hour + 1):
        result.extend(patients_by_hour.get(h, []))
    return result


def get_patient(subject_id: int, stay_id: int, hadm_id: int) -> dict | None:
    """Return a single patient dict or None."""
    p = patients_by_stay.get(stay_id)
    if p and p['subject_id'] == subject_id and p['hadm_id'] == hadm_id:
        return p
    return None


def get_prediction_at(stay_id: int, hour: int) -> dict:
    """Return cached prediction dict for a patient at a given hour."""
    return predictions.get((stay_id, hour), {"ok": False, "risk_score": None})


def get_data_up_to(cache_dict: dict, stay_id: int, hour: int) -> list[dict]:
    """Return all rows for stay_id from hours 0..hour (inclusive), sorted by hour."""
    result = []
    for h in range(0, hour + 1):
        result.extend(cache_dict.get((stay_id, h), []))
    return result


# ---------------------------------------------------------------------------
# Internal loaders
# ---------------------------------------------------------------------------

def _load_patients():
    """Load sim_cache_icustays into patients_by_hour and patients_by_stay."""
    sql = """
        SELECT subject_id, anchor_age, gender, race, hadm_id, stay_id,
               first_careunit, last_careunit, intime, outtime, los, intime_hour
        FROM simulation.sim_cache_icustays
    """
    rows = _run_query(sql)
    for row in rows:
        hour = row['intime_hour']
        patients_by_hour.setdefault(hour, []).append(row)
        patients_by_stay[row['stay_id']] = row
    logger.info("[demo_cache] patients: %d rows, %d distinct hours",
                len(rows), len(patients_by_hour))


def _load_hourly(table: str, target: dict, columns: list[str]):
    """Load an hourly cache table into a {(stay_id, hour_int): [row]} dict."""
    col_str = ', '.join(columns)
    sql = f"SELECT {col_str} FROM {table}"
    rows = _run_query(sql)
    for row in rows:
        ct = row['charttime_hour']
        if hasattr(ct, 'hour'):
            hour = ct.hour
        else:
            hour = int(ct)
        key = (row['stay_id'], hour)
        target.setdefault(key, []).append(row)
    logger.info("[demo_cache] %s: %d rows", table, len(rows))


def _precompute_predictions():
    """Pre-compute predictions for all patients at each hour (0-23)."""
    from . import model_local
    from .services import _get_prediction_stub, _normalize_hour

    count = 0
    for stay_id, patient in patients_by_stay.items():
        subject_id = patient['subject_id']
        hadm_id = patient['hadm_id']

        for hour in range(24):
            # Build source rows from in-memory cache (up to this hour)
            source_data = {}

            # Vitals (required anchor)
            vitals_rows = []
            for h in range(hour + 1):
                vitals_rows.extend(vitals.get((stay_id, h), []))
            if not vitals_rows:
                continue
            source_data['vitals_hourly'] = vitals_rows[-1]  # latest row

            # Optional sources: chemistry, coagulation, sofa
            for source_name, cache_dict in [
                ('chemistry_hourly', chemistry),
                ('coagulation_hourly', coagulation),
                ('sofa_hourly', sofa),
            ]:
                rows = []
                for h in range(hour + 1):
                    rows.extend(cache_dict.get((stay_id, h), []))
                if rows:
                    source_data[source_name] = rows[-1]

            # Predict
            if model_local.is_available():
                result = model_local.predict(source_data)
            else:
                # Build as_of datetime for stub
                from .utils import SIM_YEAR, SIM_MONTH, SIM_DAY
                as_of = datetime(SIM_YEAR, SIM_MONTH, SIM_DAY, hour)
                result = _get_prediction_stub(subject_id, stay_id, hadm_id, as_of)

            predictions[(stay_id, hour)] = result
            count += 1

    logger.info("[demo_cache] Pre-computed %d predictions", count)


def _run_query(sql: str) -> list[dict]:
    with connection.cursor() as cursor:
        cursor.execute(sql)
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
