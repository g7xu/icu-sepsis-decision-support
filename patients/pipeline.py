"""
Simulation pipeline: advance_hour() and rewind_hour().

advance_hour(H):
  - Reads from simulation.sim_cache_* tables (pre-loaded by preload_cohort_cache command)
  - Inserts newly admitted patients and one hour of measurements into sim_* tables
  - Called by views.advance_time() and the auto-play background thread

rewind_hour(H):
  - Deletes all sim_* rows for hour H
  - Removes patients admitted at hour H
  - Called by views.rewind_time() and the auto-play-backward thread
  - Cache tables are NOT touched by rewind (they're permanent infrastructure)

Simulation date is fixed: March 13 (any year — MIMIC uses shifted years).
All charttime_hour values stored as 2025-03-13 HH:00:00 for consistent display.

Performance note:
  Each advance_hour() reads from tiny sim_cache_* tables (≤60 rows per query)
  instead of scanning millions of rows in MIMIC source tables.
  Run `python manage.py preload_cohort_cache` once to populate the cache.
"""

import logging
from datetime import datetime

from django.db import connection

from .cohort import PATIENT_STAYS
from .models import (
    SimPatient,
    SimVitalsignHourly,
    SimProcedureeventsHourly,
    SimChemistryHourly,
    SimCoagulationHourly,
    SimSofaHourly,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cohort constants (derived from cohort.py at import time)
# ---------------------------------------------------------------------------

COHORT_STAY_IDS = [stay_id for _, stay_id, _ in PATIENT_STAYS]

# Map stay_id → (subject_id, hadm_id) for joins that need it
STAY_TO_IDS = {stay_id: (subject_id, hadm_id) for subject_id, stay_id, hadm_id in PATIENT_STAYS}

# Simulation display date — used for normalizing stored charttime_hour values
SIM_YEAR, SIM_MONTH, SIM_DAY = 2025, 3, 13


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def advance_hour(current_hour: int) -> dict:
    """
    Copy one hour of pre-cached MIMIC data into the sim_* tables.
    Returns a summary dict (mirrors the old advance_time JSON response shape).
    """
    hour_start = datetime(SIM_YEAR, SIM_MONTH, SIM_DAY, current_hour, 0, 0)
    logger.info("[pipeline] advance_hour(%d) — %s", current_hour, hour_start)

    # 1. Admit new patients whose intime_hour == current_hour (from cache)
    try:
        new_patients = _fetch_new_admissions(current_hour)
    except Exception as exc:
        logger.error("[pipeline] _fetch_new_admissions FAILED: %s", exc, exc_info=True)
        raise
    logger.info("[pipeline] _fetch_new_admissions → %d patient(s)", len(new_patients))

    if new_patients:
        try:
            SimPatient.objects.bulk_create(new_patients, ignore_conflicts=True)
            logger.info("[pipeline] SimPatient.bulk_create OK (%d rows)", len(new_patients))
        except Exception as exc:
            logger.error("[pipeline] SimPatient.bulk_create FAILED: %s", exc, exc_info=True)
            raise

    # 2. Get all stay_ids admitted so far
    admitted_stay_ids = list(SimPatient.objects.values_list('stay_id', flat=True))
    logger.info("[pipeline] total admitted stay_ids so far: %d", len(admitted_stay_ids))

    vitalsigns_count = 0
    procedures_count = 0

    if admitted_stay_ids:
        # 3. Vitals
        vitals = _fetch_vitals_for_hour(admitted_stay_ids, current_hour)
        if vitals:
            SimVitalsignHourly.objects.bulk_create(vitals)
            vitalsigns_count = len(vitals)
            logger.info("[pipeline] vitals inserted: %d", vitalsigns_count)

        # 4. Procedures
        procs = _fetch_procedures_for_hour(admitted_stay_ids, current_hour)
        if procs:
            SimProcedureeventsHourly.objects.bulk_create(procs)
            procedures_count = len(procs)
            logger.info("[pipeline] procedures inserted: %d", procedures_count)

        # 5. Chemistry
        chem = _fetch_chemistry_for_hour(admitted_stay_ids, current_hour)
        if chem:
            SimChemistryHourly.objects.bulk_create(chem)
            logger.info("[pipeline] chemistry inserted: %d", len(chem))

        # 6. Coagulation
        coag = _fetch_coagulation_for_hour(admitted_stay_ids, current_hour)
        if coag:
            SimCoagulationHourly.objects.bulk_create(coag)
            logger.info("[pipeline] coagulation inserted: %d", len(coag))

        # 7. SOFA (optional — skips gracefully if cache table empty/absent)
        sofa = _fetch_sofa_for_hour(admitted_stay_ids, current_hour)
        if sofa:
            SimSofaHourly.objects.bulk_create(sofa)
            logger.info("[pipeline] sofa inserted: %d", len(sofa))

    new_patient_data = [
        {
            'subject_id': p.subject_id,
            'stay_id': p.stay_id,
            'hadm_id': p.hadm_id,
            'anchor_age': p.anchor_age,
            'gender': p.gender,
            'first_careunit': p.first_careunit,
            'intime': p.intime.isoformat() if p.intime else None,
        }
        for p in new_patients
    ]

    return {
        'current_hour': current_hour,
        'current_time': _display_time(current_hour),
        'new_patients': new_patient_data,
        'new_patients_count': len(new_patients),
        'total_admitted': len(admitted_stay_ids),
        'vitalsigns_count': vitalsigns_count,
        'procedureevents_count': procedures_count,
    }


def rewind_hour(hour_to_remove: int) -> None:
    """
    Delete all sim_* rows for hour_to_remove and remove patients admitted at that hour.
    Cache tables (sim_cache_*) are NOT modified — they're permanent infrastructure.
    """
    rewind_dt = datetime(SIM_YEAR, SIM_MONTH, SIM_DAY, hour_to_remove, 0, 0)
    logger.info("[pipeline] rewind_hour(%d) — deleting rows with charttime_hour=%s", hour_to_remove, rewind_dt)

    SimVitalsignHourly.objects.filter(charttime_hour=rewind_dt).delete()
    SimProcedureeventsHourly.objects.filter(charttime_hour=rewind_dt).delete()
    SimChemistryHourly.objects.filter(charttime_hour=rewind_dt).delete()
    SimCoagulationHourly.objects.filter(charttime_hour=rewind_dt).delete()
    SimSofaHourly.objects.filter(charttime_hour=rewind_dt).delete()

    # Remove patients admitted on March 13 at this hour (any year — MIMIC year-shifted)
    SimPatient.objects.filter(
        intime__month=SIM_MONTH,
        intime__day=SIM_DAY,
        intime__hour=hour_to_remove,
    ).delete()


# ---------------------------------------------------------------------------
# Private helpers — all read from sim_cache_* tables (tiny, indexed, fast)
# ---------------------------------------------------------------------------

def _display_time(current_hour: int) -> str:
    display_hour = current_hour + 1
    if display_hour <= 0:
        return "March 13, 2025 00:00"
    elif display_hour >= 24:
        return "March 14, 2025 00:00"
    return f"March 13, 2025 {display_hour:02d}:00"


def _fetch_new_admissions(current_hour: int) -> list:
    """
    Return SimPatient objects for cohort patients admitted at current_hour.
    Reads from sim_cache_icustays (intime_hour column) — no MIMIC scan needed.
    """
    sql = """
        SELECT subject_id, anchor_age, gender, race, hadm_id, stay_id,
               first_careunit, last_careunit, intime, outtime, los
        FROM simulation.sim_cache_icustays
        WHERE intime_hour = %s
    """
    rows = _run_query(sql, [current_hour])
    return [
        SimPatient(
            subject_id=r['subject_id'],
            anchor_age=r['anchor_age'],
            gender=r['gender'],
            race=r['race'],
            hadm_id=r['hadm_id'],
            stay_id=r['stay_id'],
            first_careunit=r['first_careunit'],
            last_careunit=r['last_careunit'],
            intime=r['intime'],
            outtime=r['outtime'],
            los=r['los'],
        )
        for r in rows
    ]


def _fetch_vitals_for_hour(stay_ids: list, current_hour: int) -> list:
    """
    Reads pre-aggregated vitals from sim_cache_vitalsign_hourly.
    Filters by admitted stay_ids and normalized charttime_hour.
    """
    sql = """
        SELECT subject_id, stay_id, charttime_hour,
               heart_rate, sbp, dbp, mbp, sbp_ni, dbp_ni, mbp_ni,
               resp_rate, temperature, temperature_site, spo2, glucose
        FROM simulation.sim_cache_vitalsign_hourly
        WHERE stay_id = ANY(%s)
          AND charttime_hour = MAKE_TIMESTAMP(2025, 3, 13, %s, 0, 0)
    """
    rows = _run_query(sql, [stay_ids, current_hour])
    return [
        SimVitalsignHourly(
            subject_id=r['subject_id'],
            stay_id=r['stay_id'],
            charttime_hour=r['charttime_hour'],
            heart_rate=r['heart_rate'],
            sbp=r['sbp'],
            dbp=r['dbp'],
            mbp=r['mbp'],
            sbp_ni=r['sbp_ni'],
            dbp_ni=r['dbp_ni'],
            mbp_ni=r['mbp_ni'],
            resp_rate=r['resp_rate'],
            temperature=r['temperature'],
            temperature_site=r['temperature_site'],
            spo2=r['spo2'],
            glucose=r['glucose'],
        )
        for r in rows
    ]


def _fetch_procedures_for_hour(stay_ids: list, current_hour: int) -> list:
    """
    Reads pre-filtered procedure events from sim_cache_procedures.
    """
    sql = """
        SELECT subject_id, stay_id, charttime_hour, charttime, caregiver_id,
               itemid, item_label, item_unitname, item_lownormalvalue, item_highnormalvalue,
               value, valueuom, location, locationcategory, orderid, linkorderid,
               ordercategoryname, ordercategorydescription, patientweight,
               isopenbag, continueinnextdept, statusdescription,
               originalamount, originalrate
        FROM simulation.sim_cache_procedures
        WHERE stay_id = ANY(%s)
          AND charttime_hour = MAKE_TIMESTAMP(2025, 3, 13, %s, 0, 0)
    """
    rows = _run_query(sql, [stay_ids, current_hour])
    return [
        SimProcedureeventsHourly(
            subject_id=r['subject_id'],
            stay_id=r['stay_id'],
            charttime_hour=r['charttime_hour'],
            charttime=r['charttime'],
            caregiver_id=r['caregiver_id'],
            itemid=r['itemid'],
            item_label=r['item_label'],
            item_unitname=r['item_unitname'],
            item_lownormalvalue=r['item_lownormalvalue'],
            item_highnormalvalue=r['item_highnormalvalue'],
            value=r['value'],
            valueuom=r['valueuom'],
            location=r['location'],
            locationcategory=r['locationcategory'],
            orderid=r['orderid'],
            linkorderid=r['linkorderid'],
            ordercategoryname=r['ordercategoryname'],
            ordercategorydescription=r['ordercategorydescription'],
            patientweight=r['patientweight'],
            isopenbag=r['isopenbag'],
            continueinnextdept=r['continueinnextdept'],
            statusdescription=r['statusdescription'],
            originalamount=r['originalamount'],
            originalrate=r['originalrate'],
        )
        for r in rows
    ]


def _fetch_chemistry_for_hour(stay_ids: list, current_hour: int) -> list:
    """
    Reads pre-aggregated chemistry from sim_cache_chemistry_hourly.
    """
    sql = """
        SELECT subject_id, stay_id, charttime_hour,
               bicarbonate, calcium, sodium, potassium
        FROM simulation.sim_cache_chemistry_hourly
        WHERE stay_id = ANY(%s)
          AND charttime_hour = MAKE_TIMESTAMP(2025, 3, 13, %s, 0, 0)
    """
    rows = _run_query(sql, [stay_ids, current_hour])
    return [
        SimChemistryHourly(
            subject_id=r['subject_id'],
            stay_id=r['stay_id'],
            charttime_hour=r['charttime_hour'],
            bicarbonate=r['bicarbonate'],
            calcium=r['calcium'],
            sodium=r['sodium'],
            potassium=r['potassium'],
        )
        for r in rows
    ]


def _fetch_coagulation_for_hour(stay_ids: list, current_hour: int) -> list:
    """
    Reads pre-aggregated coagulation from sim_cache_coagulation_hourly.
    """
    sql = """
        SELECT subject_id, stay_id, charttime_hour,
               d_dimer, fibrinogen, thrombin, inr, pt, ptt
        FROM simulation.sim_cache_coagulation_hourly
        WHERE stay_id = ANY(%s)
          AND charttime_hour = MAKE_TIMESTAMP(2025, 3, 13, %s, 0, 0)
    """
    rows = _run_query(sql, [stay_ids, current_hour])
    return [
        SimCoagulationHourly(
            subject_id=r['subject_id'],
            stay_id=r['stay_id'],
            charttime_hour=r['charttime_hour'],
            d_dimer=r['d_dimer'],
            fibrinogen=r['fibrinogen'],
            thrombin=r['thrombin'],
            inr=r['inr'],
            pt=r['pt'],
            ptt=r['ptt'],
        )
        for r in rows
    ]


def _fetch_sofa_for_hour(stay_ids: list, current_hour: int) -> list:
    """
    Reads SOFA scores from sim_cache_sofa_hourly.
    Returns empty list if cache table has no data (sofa_hourly not available on RDS).
    """
    sql = """
        SELECT subject_id, stay_id, charttime_hour,
               sofa_24hours, respiration, coagulation, liver,
               cardiovascular, cns, renal
        FROM simulation.sim_cache_sofa_hourly
        WHERE stay_id = ANY(%s)
          AND charttime_hour = MAKE_TIMESTAMP(2025, 3, 13, %s, 0, 0)
    """
    try:
        rows = _run_query(sql, [stay_ids, current_hour])
    except Exception:
        return []
    return [
        SimSofaHourly(
            subject_id=r['subject_id'],
            stay_id=r['stay_id'],
            charttime_hour=r['charttime_hour'],
            sofa_24hours=r.get('sofa_24hours'),
            respiration=r.get('respiration'),
            coagulation=r.get('coagulation'),
            liver=r.get('liver'),
            cardiovascular=r.get('cardiovascular'),
            cns=r.get('cns'),
            renal=r.get('renal'),
        )
        for r in rows
    ]


def _run_query(sql: str, params: list) -> list:
    """Execute a raw SQL query and return list of dicts."""
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
