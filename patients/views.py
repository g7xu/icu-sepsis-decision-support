"""
Patient views - handles patient list, detail pages, and simulation clock API.

Demo date alignment: We display "March 13" for the simulation, but patients may be
from any admission date. Data queries use each patient's actual intime to map
simulation hour -> charttime_hour (hours since admission).
"""

import json
import uuid
from datetime import datetime, timedelta

from django.shortcuts import render, get_object_or_404
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .cohort import get_cohort_filter
from .display_names import DISPLAY_NAMES, get_display_name_mapping
from .models import (
    ChemistryHourly,
    CoagulationHourly,
    ProcedureeventsHourly,
    SofaHourly,
    UniquePatientProfile,
    VitalsignHourly,
)
from .services import get_prediction
from .session_utils import (
    get_prediction_cached,
    get_similar_patients_cached,
    store_prediction,
)

# =============================================================================
# Session-based simulation state (per-user)
# Requires SESSION_ENGINE = db and django.contrib.sessions (run migrate)
# Session is invalidated on server restart so clock resets to 00:00
# =============================================================================

_server_instance_id = uuid.uuid4().hex  # Changes on each server restart


def _is_session_stale(request):
    return request.session.get("_server_id") != _server_instance_id


def _clear_stale_session(request):
    for key in ("simulation_hour", "predictions", "similar_patients", "_server_id"):
        request.session.pop(key, None)
    request.session.modified = True


def _ensure_session_fresh(request):
    if _is_session_stale(request):
        _clear_stale_session(request)
        request.session["_server_id"] = _server_instance_id
        request.session.modified = True


def _get_simulation_hour(request):
    """Return current simulation hour from session (-1 = not started)."""
    _ensure_session_fresh(request)
    return request.session.get("simulation_hour", -1)


def _set_simulation_hour(request, hour):
    _ensure_session_fresh(request)
    request.session["simulation_hour"] = hour
    request.session["_server_id"] = _server_instance_id
    request.session.modified = True


# =============================================================================
# Helper functions
# =============================================================================

def _display_time(current_hour):
    """Frontend clock string; offset by +1 so the first click shows 01:00."""
    display_hour = current_hour + 1
    if display_hour <= 0 or display_hour >= 24:
        return "00:00"
    return f"{display_hour:02d}:00"


def _time_since_admission(intime, current_hour):
    """Return (display_string, total_minutes) relative to admission time-of-day."""
    if current_hour < 0 or intime is None:
        return ("-", -1)
    sim_minutes = (current_hour + 1) * 60 if current_hour < 23 else 24 * 60
    adm_minutes = intime.hour * 60 + intime.minute
    delta = sim_minutes - adm_minutes
    if delta < 0:
        delta += 24 * 60  # Overnight (e.g. admitted 22:00, sim 06:00).
    hours, minutes = divmod(delta, 60)
    return (f"{hours:02d}:{minutes:02d}", delta)


def _prediction_as_of_dt(current_hour, patient_intime=None):
    """Normalized 2025-03-13 timestamp for the UI clock. DB queries are year-agnostic."""
    if current_hour < 0:
        return None
    if current_hour >= 23:
        return datetime(2025, 3, 14, 0, 0, 0)
    return datetime(2025, 3, 13, current_hour + 1, 0, 0)


def _get_cohort_patients():
    """Base queryset of cohort patients (no date filter — cohort may span years)."""
    patients = UniquePatientProfile.objects.all()

    cohort = get_cohort_filter()
    if cohort:
        if cohort["type"] == "subject_ids":
            patients = patients.filter(subject_id__in=cohort["values"])
        elif cohort["type"] == "tuples":
            conditions = Q()
            for subject_id, stay_id, hadm_id in cohort["values"]:
                conditions |= Q(subject_id=subject_id, stay_id=stay_id, hadm_id=hadm_id)
            patients = patients.filter(conditions)

    return patients


def _get_admitted_patients(current_hour):
    """Patients whose admission hour-of-day is <= current_hour."""
    if current_hour < 0:
        return UniquePatientProfile.objects.none()
    return _get_cohort_patients().filter(intime__hour__lte=current_hour)


def _patient_charttime_range(patient_intime, current_hour):
    """Map simulation hour to [start, end] charttime range for this patient."""
    if not patient_intime or current_hour < 0:
        return None, None
    adm_hour = patient_intime.hour
    if current_hour < adm_hour:
        return None, None
    base = patient_intime.replace(minute=0, second=0, microsecond=0)
    return base, base + timedelta(hours=current_hour - adm_hour)


def _hour_label(charttime, intime, start_hour):
    """Clock-style label for a chart row, e.g. '09:00' relative to admission."""
    hours_since = (charttime - start_hour).total_seconds() / 3600
    total_minutes = intime.hour * 60 + intime.minute + int(hours_since * 60)
    display_h = (total_minutes // 60) % 24
    display_m = total_minutes % 60
    return int(hours_since), f"{display_h:02d}:{display_m:02d}"


def _annotate_hour_label(row, start_hour, intime):
    """Annotate a charttime row in-place with hours_since_admission + hour_label."""
    hours_since, label = _hour_label(row["charttime_hour"], intime, start_hour)
    row["hours_since_admission"] = hours_since
    row["hour_label"] = label


def _fetch_chart_rows(model_class, subject_id, stay_id, fields, start_hour, end_hour, intime):
    """Fetch hourly rows for a chart (vitals/chemistry/coagulation), annotated for display."""
    if start_hour is None or end_hour is None:
        return []
    qs = model_class.objects.filter(
        subject_id=subject_id,
        stay_id=stay_id,
        charttime_hour__gte=start_hour,
        charttime_hour__lte=end_hour,
    ).order_by("charttime_hour")

    rows = []
    for row in qs.values("charttime_hour", *fields):
        _annotate_hour_label(row, start_hour, intime)
        rows.append(row)
    return rows


def _patient_as_of_dt(patient_intime, current_hour):
    """Map simulation hour to the patient's actual as_of timestamp for predictions."""
    if not patient_intime or current_hour < 0:
        return None
    adm_hour = patient_intime.hour
    if current_hour < adm_hour:
        return None
    base = patient_intime.replace(minute=0, second=0, microsecond=0)
    return base + timedelta(hours=current_hour - adm_hour + 1)


def _assign_display_name(name_mapping, subject_id, stay_id, hadm_id):
    """Return the cohort display name for a patient, or a deterministic fallback."""
    cohort_name = name_mapping.get((subject_id, stay_id, hadm_id))
    if cohort_name:
        return cohort_name
    return DISPLAY_NAMES[subject_id % len(DISPLAY_NAMES)]


def _charttime_conditions(admitted_patients, current_hour):
    """Build Q() matching each admitted patient's charttime at this sim hour."""
    conditions = Q()
    matched = False
    for p in admitted_patients:
        intime = p.get("intime")
        if not intime or intime.hour > current_hour:
            continue
        target = intime.replace(minute=0, second=0, microsecond=0) + timedelta(
            hours=current_hour - intime.hour
        )
        conditions |= Q(stay_id=p["stay_id"], charttime_hour=target)
        matched = True
    return conditions if matched else None


# =============================================================================
# Views
# =============================================================================

def patient_list(request):
    """Patient list at /patients/. Predictions read from session cache only."""
    current_hour = _get_simulation_hour(request)
    patients = _get_admitted_patients(current_hour)

    name_mapping = get_display_name_mapping()

    patients_list = []
    for p in patients:
        p.display_name = name_mapping.get(
            (p.subject_id, p.stay_id, p.hadm_id), f"Patient {p.subject_id}"
        )

        p.risk_score = None
        p.comorbidity_group = None
        if current_hour >= 0:
            cached = get_prediction_cached(
                request.session, p.subject_id, p.stay_id, p.hadm_id, current_hour
            )
            if cached:
                rs = cached.get("risk_score")
                p.risk_score = (rs * 100) if rs is not None else None
                p.comorbidity_group = cached.get("comorbidity_group")

        p.time_since_admission, p.time_since_minutes = _time_since_admission(p.intime, current_hour)
        patients_list.append(p)

    return render(request, "patients/index.html", {
        "patients": patients_list,
        "total_patients": len(patients_list),
        "cohort_active": get_cohort_filter() is not None,
        "current_hour": current_hour,
        "current_time_display": _display_time(current_hour),
    })


def patient_detail(request, subject_id, stay_id, hadm_id):
    """Patient detail page with charts and procedure log up to current sim hour."""
    patient = get_object_or_404(
        UniquePatientProfile, subject_id=subject_id, stay_id=stay_id, hadm_id=hadm_id
    )

    current_hour = _get_simulation_hour(request)
    vitalsigns_list = []
    chemistry_list = []
    coagulation_list = []
    procedures = []
    procedures_non_empty_count = 0

    if current_hour >= 0:
        start_hour, end_hour = _patient_charttime_range(patient.intime, current_hour)

        vitalsigns_list = _fetch_chart_rows(
            VitalsignHourly, subject_id, stay_id,
            ["heart_rate", "sbp", "dbp", "mbp", "resp_rate", "temperature", "spo2", "glucose"],
            start_hour, end_hour, patient.intime,
        )
        chemistry_list = _fetch_chart_rows(
            ChemistryHourly, subject_id, stay_id,
            ["bicarbonate", "calcium", "sodium", "potassium"],
            start_hour, end_hour, patient.intime,
        )
        coagulation_list = _fetch_chart_rows(
            CoagulationHourly, subject_id, stay_id,
            ["d_dimer", "fibrinogen", "thrombin", "inr", "pt", "ptt"],
            start_hour, end_hour, patient.intime,
        )

        if start_hour is not None and end_hour is not None:
            procedures = list(ProcedureeventsHourly.objects.filter(
                subject_id=subject_id,
                stay_id=stay_id,
                charttime_hour__gte=start_hour,
                charttime_hour__lte=end_hour,
                itemid__isnull=False,
            ).order_by("charttime_hour").values(
                "charttime_hour", "charttime",
                "item_label", "value", "valueuom",
                "ordercategoryname", "statusdescription",
            ))[::-1]
            procedures_non_empty_count = sum(1 for p in procedures if p.get("item_label"))

    as_of_dt = _prediction_as_of_dt(current_hour)
    prediction_as_of_iso = as_of_dt.isoformat() if as_of_dt else None

    name_mapping = get_display_name_mapping()
    patient.display_name = name_mapping.get(
        (patient.subject_id, patient.stay_id, patient.hadm_id), f"Patient {patient.subject_id}"
    )
    patient.time_since_admission, _ = _time_since_admission(patient.intime, current_hour)

    patient.risk_score = None
    if current_hour >= 0:
        cached = get_prediction_cached(
            request.session, patient.subject_id, patient.stay_id, patient.hadm_id, current_hour
        )
        if cached:
            rs = cached.get("risk_score")
            patient.risk_score = (rs * 100) if rs is not None else None

    return render(request, "patients/show.html", {
        "patient": patient,
        "vitalsigns_json": json.dumps(vitalsigns_list, cls=DjangoJSONEncoder),
        "chemistry_json": json.dumps(chemistry_list, cls=DjangoJSONEncoder),
        "coagulation_json": json.dumps(coagulation_list, cls=DjangoJSONEncoder),
        "procedures": procedures,
        "procedures_count": len(procedures),
        "procedures_non_empty_count": procedures_non_empty_count,
        "current_hour": current_hour,
        "current_time_display": _display_time(current_hour),
        "prediction_as_of_iso": prediction_as_of_iso,
        "admission_hour": patient.intime.hour if patient.intime else 0,
        "admission_minute": patient.intime.minute if patient.intime else 0,
    })


@require_POST
def advance_time(request):
    """Advance the simulation clock by 1 hour and score all admitted patients."""
    import traceback
    try:
        return _advance_time_impl(request)
    except Exception as e:
        return JsonResponse({"error": str(e), "traceback": traceback.format_exc()}, status=500)


def _advance_time_impl(request):
    current_hour = _get_simulation_hour(request) + 1
    _set_simulation_hour(request, current_hour)

    if current_hour > 23:
        _set_simulation_hour(request, 23)
        return JsonResponse({
            "error": "Cannot advance past 23:00",
            "current_hour": 23,
            "current_time": _display_time(23),
        }, status=400)

    # 1. Patients newly admitted at this hour.
    new_patients_data = list(
        _get_cohort_patients().filter(intime__hour=current_hour).values(
            "subject_id", "stay_id", "hadm_id",
            "anchor_age", "gender", "race",
            "first_careunit", "intime", "outtime", "los",
        )
    )

    # 2. All currently admitted patients.
    admitted_patients = list(
        _get_admitted_patients(current_hour).values("subject_id", "stay_id", "hadm_id", "intime")
    )
    admitted_stay_ids = [p["stay_id"] for p in admitted_patients]

    # 3. Vitals + procedures at this hour for admitted patients.
    conditions = _charttime_conditions(admitted_patients, current_hour)
    vitalsigns_data = []
    procedures_data = []
    if conditions is not None:
        vitalsigns_data = list(VitalsignHourly.objects.filter(conditions).values(
            "subject_id", "stay_id", "charttime_hour",
            "heart_rate", "sbp", "dbp", "mbp",
            "sbp_ni", "dbp_ni", "mbp_ni",
            "resp_rate", "temperature", "temperature_site",
            "spo2", "glucose",
        ))
        procedures_data = list(ProcedureeventsHourly.objects.filter(conditions).values(
            "subject_id", "stay_id", "charttime_hour", "charttime",
            "itemid", "item_label", "item_unitname",
            "value", "valueuom",
            "location", "locationcategory",
            "ordercategoryname", "ordercategorydescription",
            "statusdescription", "originalamount", "originalrate",
        ))

    # 4. Score admitted patients (the only place the model runs).
    model_scoring_table = []
    display_as_of = _prediction_as_of_dt(current_hour)
    display_as_of_iso = display_as_of.isoformat() if display_as_of else None

    for patient in admitted_patients:
        actual_as_of = _patient_as_of_dt(patient.get("intime"), current_hour)
        if actual_as_of is None:
            continue
        pred = get_prediction(
            subject_id=patient["subject_id"],
            stay_id=patient["stay_id"],
            hadm_id=patient["hadm_id"],
            as_of=actual_as_of,
            window_hours=24,
        )
        base_entry = {
            "subject_id": patient["subject_id"],
            "stay_id": patient["stay_id"],
            "hadm_id": patient["hadm_id"],
            "as_of": display_as_of_iso,
        }
        if pred.get("ok"):
            store_prediction(
                request.session,
                patient["subject_id"], patient["stay_id"], patient["hadm_id"],
                current_hour,
                pred.get("risk_score"), pred.get("comorbidity_group"),
            )
            model_scoring_table.append({
                **base_entry,
                "risk_score": pred.get("risk_score"),
                "comorbidity_group": pred.get("comorbidity_group"),
                "ok": True,
            })
        else:
            model_scoring_table.append({
                **base_entry,
                "ok": False,
                "error": pred.get("error", "prediction failed"),
            })

    return JsonResponse({
        "current_hour": current_hour,
        "current_time": _display_time(current_hour),
        "new_patients": new_patients_data,
        "new_patients_count": len(new_patients_data),
        "total_admitted": len(admitted_stay_ids),
        "vitalsigns": vitalsigns_data,
        "vitalsigns_count": len(vitalsigns_data),
        "procedureevents": procedures_data,
        "procedureevents_count": len(procedures_data),
        "model_scoring_table": model_scoring_table,
        "model_scoring_count": len(model_scoring_table),
    })


def patient_prediction(request, subject_id, stay_id, hadm_id):
    """Prediction detail view. Reads predictions from session cache only."""
    patient = get_object_or_404(
        UniquePatientProfile, subject_id=subject_id, stay_id=stay_id, hadm_id=hadm_id
    )

    current_hour = _get_simulation_hour(request)
    as_of_dt = _prediction_as_of_dt(current_hour)

    risk_score = None
    comorbidity_group = None
    prediction_error = None
    if current_hour >= 0:
        cached = get_prediction_cached(
            request.session, subject_id, stay_id, hadm_id, current_hour
        )
        if cached:
            risk_score = cached.get("risk_score")
            comorbidity_group = cached.get("comorbidity_group")
        else:
            prediction_error = "No prediction yet. Advance time (+1) to compute."

    similar_patients = []
    load_similar_async = False
    if as_of_dt:
        cached_similar = get_similar_patients_cached(
            request.session, subject_id, stay_id, hadm_id, current_hour
        )
        if cached_similar is not None:
            similar_patients = cached_similar
        else:
            load_similar_async = True  # Client will fetch via API so the page loads fast.

    name_mapping = get_display_name_mapping()
    patient.display_name = name_mapping.get(
        (patient.subject_id, patient.stay_id, patient.hadm_id), f"Patient {patient.subject_id}"
    )
    patient.time_since_admission, _ = _time_since_admission(patient.intime, current_hour)
    patient.risk_score = (risk_score * 100) if risk_score is not None else None

    for sp in similar_patients:
        sp["display_name"] = _assign_display_name(
            name_mapping, sp["subject_id"], sp["stay_id"], sp["hadm_id"]
        )

    # Predictions-by-hour chart data (session cache only; no model calls).
    predictions_list = []
    model_sepsis_hour_index = None
    if current_hour >= 0:
        for h in range(current_hour + 1):
            as_of_h = _prediction_as_of_dt(h)
            if not as_of_h:
                continue
            cached = get_prediction_cached(
                request.session, subject_id, stay_id, hadm_id, h
            )
            rs = cached.get("risk_score") if cached else None
            pct = (rs * 100) if rs is not None else None
            predictions_list.append({
                "hour_label": f"{as_of_h.hour:02d}:{as_of_h.minute:02d}",
                "hour_val": h + 1,
                "risk_score": pct,
            })
            if model_sepsis_hour_index is None and pct is not None and pct >= 30:
                model_sepsis_hour_index = len(predictions_list) - 1

    predictions_meta_json = json.dumps({
        "model_sepsis_hour_index": model_sepsis_hour_index if current_hour >= 0 else None,
        "actual_sepsis_x": None,
        "admission_hour": patient.intime.hour if getattr(patient, "intime", None) else 0,
        "admission_minute": patient.intime.minute if getattr(patient, "intime", None) else 0,
    }, cls=DjangoJSONEncoder)

    # SOFA charts up to the current simulation hour.
    sofa_24_list = []
    sofa_other_list = []
    if current_hour >= 0:
        start_hour, end_hour = _patient_charttime_range(patient.intime, current_hour)
        if start_hour is not None and end_hour is not None:
            sofa_24_cols = [
                "respiration_24hours", "coagulation_24hours", "liver_24hours",
                "cardiovascular_24hours", "cns_24hours", "renal_24hours", "sofa_24hours",
            ]
            sofa_other_cols = [
                "pao2fio2ratio_novent", "pao2fio2ratio_vent",
                "rate_epinephrine", "rate_norepinephrine", "rate_dopamine", "rate_dobutamine",
                "meanbp_min", "gcs_min", "uo_24hr",
                "bilirubin_max", "creatinine_max", "platelet_min",
                "respiration", "coagulation", "liver", "cardiovascular", "cns", "renal",
            ]
            sofa_qs = SofaHourly.objects.filter(
                subject_id=subject_id,
                stay_id=stay_id,
                charttime_hour__gte=start_hour,
                charttime_hour__lte=end_hour,
            ).order_by("charttime_hour")

            for row in sofa_qs.values("charttime_hour", *sofa_24_cols, *sofa_other_cols):
                _, label = _hour_label(row["charttime_hour"], patient.intime, start_hour)
                sofa_24_list.append({"hour_label": label, **{c: row[c] for c in sofa_24_cols}})
                sofa_other_list.append({"hour_label": label, **{c: row[c] for c in sofa_other_cols}})

    return render(request, "patients/prediction.html", {
        "patient": patient,
        "risk_score": risk_score,
        "comorbidity_group": comorbidity_group,
        "prediction_error": prediction_error,
        "similar_patients": similar_patients,
        "load_similar_async": load_similar_async,
        "current_hour": current_hour,
        "current_time_display": _display_time(current_hour),
        "predictions_json": json.dumps(predictions_list, cls=DjangoJSONEncoder),
        "predictions_meta_json": predictions_meta_json,
        "sofa_24hours_json": json.dumps(sofa_24_list, cls=DjangoJSONEncoder),
        "sofa_other_json": json.dumps(sofa_other_list, cls=DjangoJSONEncoder),
        "prediction_as_of_iso": as_of_dt.isoformat() if as_of_dt else None,
    })
