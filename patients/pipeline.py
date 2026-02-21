"""
Simulation pipeline: advance_hour() and rewind_hour().

advance_hour(H):
  - Queries MIMIC-IV source tables directly (no fisi9t_* views needed)
  - Inserts newly admitted patients and one hour of measurements into sim_* tables
  - Called by views.advance_time() and the auto-play background thread

rewind_hour(H):
  - Deletes all sim_* rows for hour H
  - Removes patients admitted at hour H
  - Called by views.rewind_time() and the auto-play-backward thread

Simulation date is fixed: March 13 (any year — MIMIC uses shifted years).
All charttime_hour values stored as 2025-03-13 HH:00:00 for consistent display.
"""

import logging
from datetime import datetime, timedelta

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
    Fetch one hour of MIMIC-IV data for the cohort and write it to sim_* tables.
    Returns a summary dict (mirrors the old advance_time JSON response shape).
    """
    # Normalized hour stored in sim tables (MIMIC data has year-shifted timestamps)
    hour_start = datetime(SIM_YEAR, SIM_MONTH, SIM_DAY, current_hour, 0, 0)
    logger.info("[pipeline] advance_hour(%d) — simulated window %s", current_hour, hour_start)

    # 1. Admit new patients whose intime falls on March 13 at this hour (any year)
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

    # 2. Get all stay_ids admitted so far (not just this hour)
    try:
        admitted_stay_ids = list(SimPatient.objects.values_list('stay_id', flat=True))
    except Exception as exc:
        logger.error("[pipeline] SimPatient.objects.values_list FAILED: %s", exc, exc_info=True)
        raise
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

        # 7. SOFA (optional — skips gracefully if source table absent)
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
    charttime_hour is stored normalized to 2025-03-13 HH:00:00, so rewind uses that.
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
# Private helpers
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
    Return SimPatient objects for cohort patients admitted on March 13 at current_hour.
    Filters by month=3, day=13, hour=H only — no year filter (MIMIC uses shifted years).
    """
    sql = """
        SELECT
            i.subject_id,
            a.anchor_age,
            id.gender,
            id.race,
            i.hadm_id,
            i.stay_id,
            i.first_careunit,
            i.last_careunit,
            i.intime,
            i.outtime,
            i.los
        FROM mimiciv_icu.icustays i
        JOIN mimiciv_derived.age a
            ON a.subject_id = i.subject_id AND a.hadm_id = i.hadm_id
        JOIN mimiciv_derived.icustay_detail id
            ON id.stay_id = i.stay_id
        WHERE i.stay_id = ANY(%s)
          AND EXTRACT(MONTH FROM i.intime) = %s
          AND EXTRACT(DAY   FROM i.intime) = %s
          AND EXTRACT(HOUR  FROM i.intime) = %s
    """
    logger.info(
        "[pipeline] _fetch_new_admissions: month=%d day=%d hour=%d for %d cohort stay_ids",
        SIM_MONTH, SIM_DAY, current_hour, len(COHORT_STAY_IDS),
    )
    rows = _run_query(sql, [COHORT_STAY_IDS, SIM_MONTH, SIM_DAY, current_hour])
    logger.info("[pipeline] _fetch_new_admissions raw rows returned: %d", len(rows))
    if rows:
        logger.info("[pipeline] sample intime values: %s", [r.get('intime') for r in rows[:3]])
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
    Hourly AVG vitals from mimiciv_derived.vitalsign.
    Filters by March 13 at current_hour (any year). Stores normalized 2025-03-13 HH:00:00.
    """
    sql = """
        SELECT
            v.subject_id,
            v.stay_id,
            MAKE_TIMESTAMP(2025, 3, 13, EXTRACT(HOUR FROM v.charttime)::int, 0, 0) AS charttime_hour,
            AVG(v.heart_rate)     FILTER (WHERE v.heart_rate IS NOT NULL)    AS heart_rate,
            AVG(v.sbp)            FILTER (WHERE v.sbp IS NOT NULL)           AS sbp,
            AVG(v.dbp)            FILTER (WHERE v.dbp IS NOT NULL)           AS dbp,
            AVG(v.mbp)            FILTER (WHERE v.mbp IS NOT NULL)           AS mbp,
            AVG(v.sbp_ni)         FILTER (WHERE v.sbp_ni IS NOT NULL)        AS sbp_ni,
            AVG(v.dbp_ni)         FILTER (WHERE v.dbp_ni IS NOT NULL)        AS dbp_ni,
            AVG(v.mbp_ni)         FILTER (WHERE v.mbp_ni IS NOT NULL)        AS mbp_ni,
            AVG(v.resp_rate)      FILTER (WHERE v.resp_rate IS NOT NULL)     AS resp_rate,
            AVG(v.temperature)    FILTER (WHERE v.temperature IS NOT NULL)   AS temperature,
            (ARRAY_AGG(v.temperature_site ORDER BY v.charttime)
                FILTER (WHERE v.temperature_site IS NOT NULL))[1]            AS temperature_site,
            AVG(v.spo2)           FILTER (WHERE v.spo2 IS NOT NULL)          AS spo2,
            AVG(v.glucose)        FILTER (WHERE v.glucose IS NOT NULL)       AS glucose
        FROM mimiciv_derived.vitalsign v
        WHERE v.stay_id = ANY(%s)
          AND EXTRACT(MONTH FROM v.charttime) = 3
          AND EXTRACT(DAY   FROM v.charttime) = 13
          AND EXTRACT(HOUR  FROM v.charttime) = %s
        GROUP BY v.subject_id, v.stay_id, EXTRACT(HOUR FROM v.charttime)::int
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
    Procedure events from mimiciv_icu.procedureevents + mimiciv_icu.d_items.
    Filters by March 13, hour bucket using +30min rounding. Stores normalized charttime_hour.
    """
    sql = """
        SELECT
            p.subject_id,
            p.stay_id,
            MAKE_TIMESTAMP(2025, 3, 13,
                EXTRACT(HOUR FROM (p.storetime + INTERVAL '30 minutes'))::int, 0, 0) AS charttime_hour,
            p.storetime       AS charttime,
            p.caregiver_id,
            p.itemid,
            di.label          AS item_label,
            di.unitname       AS item_unitname,
            di.lownormalvalue AS item_lownormalvalue,
            di.highnormalvalue AS item_highnormalvalue,
            p.value,
            p.valueuom,
            p.location,
            p.locationcategory,
            p.orderid,
            p.linkorderid,
            p.ordercategoryname,
            p.ordercategorydescription,
            p.patientweight,
            p.isopenbag,
            p.continueinnextdept,
            p.statusdescription,
            p.originalamount,
            p.originalrate
        FROM mimiciv_icu.procedureevents p
        LEFT JOIN mimiciv_icu.d_items di ON di.itemid = p.itemid
        WHERE p.stay_id = ANY(%s)
          AND p.storetime IS NOT NULL
          AND EXTRACT(MONTH FROM p.storetime) = 3
          AND EXTRACT(DAY   FROM p.storetime) = 13
          AND EXTRACT(HOUR  FROM (p.storetime + INTERVAL '30 minutes')) = %s
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
    Hourly chemistry labs from mimiciv_derived.chemistry.
    Keyed by subject_id; join through sim_patient to get stay_id.
    """
    sql = """
        SELECT
            ch.subject_id,
            sp.stay_id,
            MAKE_TIMESTAMP(2025, 3, 13, %s, 0, 0) AS charttime_hour,
            MIN(ch.bicarbonate) FILTER (WHERE ch.bicarbonate IS NOT NULL) AS bicarbonate,
            AVG(ch.calcium)     FILTER (WHERE ch.calcium IS NOT NULL)     AS calcium,
            AVG(ch.sodium)      FILTER (WHERE ch.sodium IS NOT NULL)      AS sodium,
            MAX(ch.potassium)   FILTER (WHERE ch.potassium IS NOT NULL)   AS potassium
        FROM mimiciv_derived.chemistry ch
        JOIN simulation.sim_patient sp ON sp.subject_id = ch.subject_id
        WHERE sp.stay_id = ANY(%s)
          AND EXTRACT(MONTH FROM ch.charttime) = 3
          AND EXTRACT(DAY   FROM ch.charttime) = 13
          AND EXTRACT(HOUR  FROM (ch.charttime + INTERVAL '30 minutes')) = %s
        GROUP BY ch.subject_id, sp.stay_id
    """
    rows = _run_query(sql, [current_hour, stay_ids, current_hour])
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
    Hourly coagulation labs from mimiciv_derived.coagulation.
    Keyed by subject_id; join through sim_patient to get stay_id.
    """
    sql = """
        SELECT
            co.subject_id,
            sp.stay_id,
            MAKE_TIMESTAMP(2025, 3, 13, %s, 0, 0) AS charttime_hour,
            (ARRAY_AGG(co.inr       ORDER BY co.charttime DESC) FILTER (WHERE co.inr IS NOT NULL))[1]       AS inr,
            (ARRAY_AGG(co.pt        ORDER BY co.charttime DESC) FILTER (WHERE co.pt IS NOT NULL))[1]        AS pt,
            (ARRAY_AGG(co.ptt       ORDER BY co.charttime DESC) FILTER (WHERE co.ptt IS NOT NULL))[1]       AS ptt,
            (ARRAY_AGG(co.thrombin  ORDER BY co.charttime DESC) FILTER (WHERE co.thrombin IS NOT NULL))[1]  AS thrombin,
            MAX(co.d_dimer)    FILTER (WHERE co.d_dimer IS NOT NULL)    AS d_dimer,
            MIN(co.fibrinogen) FILTER (WHERE co.fibrinogen IS NOT NULL) AS fibrinogen
        FROM mimiciv_derived.coagulation co
        JOIN simulation.sim_patient sp ON sp.subject_id = co.subject_id
        WHERE sp.stay_id = ANY(%s)
          AND EXTRACT(MONTH FROM co.charttime) = 3
          AND EXTRACT(DAY   FROM co.charttime) = 13
          AND EXTRACT(HOUR  FROM (co.charttime + INTERVAL '30 minutes')) = %s
        GROUP BY co.subject_id, sp.stay_id
    """
    rows = _run_query(sql, [current_hour, stay_ids, current_hour])
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
    SOFA scores from mimiciv_derived.sofa_hourly.
    Skips gracefully if the source table doesn't exist on this database.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT to_regclass('mimiciv_derived.sofa_hourly') IS NOT NULL"
        )
        exists = cursor.fetchone()[0]

    if not exists:
        return []

    sql = """
        SELECT
            s.stay_id,
            MAKE_TIMESTAMP(2025, 3, 13, %s, 0, 0) AS charttime_hour,
            s.sofa_24hours,
            s.respiration,
            s.coagulation,
            s.liver,
            s.cardiovascular,
            s.cns,
            s.renal
        FROM mimiciv_derived.sofa_hourly s
        WHERE s.stay_id = ANY(%s)
          AND EXTRACT(MONTH FROM s.starttime) = 3
          AND EXTRACT(DAY   FROM s.starttime) = 13
          AND EXTRACT(HOUR  FROM s.starttime) = %s
    """
    try:
        rows = _run_query(sql, [current_hour, stay_ids, current_hour])
    except Exception:
        return []

    result = []
    for r in rows:
        subject_id, _ = STAY_TO_IDS.get(r['stay_id'], (None, None))
        if subject_id is None:
            continue
        result.append(SimSofaHourly(
            subject_id=subject_id,
            stay_id=r['stay_id'],
            charttime_hour=r['charttime_hour'],
            sofa_24hours=r.get('sofa_24hours'),
            respiration=r.get('respiration'),
            coagulation=r.get('coagulation'),
            liver=r.get('liver'),
            cardiovascular=r.get('cardiovascular'),
            cns=r.get('cns'),
            renal=r.get('renal'),
        ))
    return result


def _run_query(sql: str, params: list) -> list:
    """Execute a raw SQL query and return list of dicts."""
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
