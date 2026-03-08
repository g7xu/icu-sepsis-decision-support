"""
Patient views - handles patient list, detail pages, and simulation clock API.

Demo date alignment: We display "March 13" for the simulation, but patients may be
from any admission date. Data queries use each patient's actual intime to map
simulation hour -> charttime_hour (hours since admission).
"""

import json
import uuid
from datetime import datetime, timedelta, timezone as dt_tz

from django.shortcuts import render, get_object_or_404
from django.core.paginator import Paginator
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from django.utils import timezone as django_tz
from django.utils.dateparse import parse_datetime

from .models import UniquePatientProfile, VitalsignHourly, ProcedureeventsHourly, ChemistryHourly, CoagulationHourly, SofaHourly
from .cohort import get_cohort_filter
from .services import get_prediction, get_sepsis3_suspected_infection_time, get_similar_patients
from .display_names import get_display_name_mapping


# =============================================================================
# Session-based simulation state (per-user)
# Requires SESSION_ENGINE = db and django.contrib.sessions (run migrate)
# Session is invalidated on server restart so clock resets to 00:00
# =============================================================================

_server_instance_id = uuid.uuid4().hex  # Changes on each server restart


def _is_session_stale(request):
    """True if session was created before this server instance started."""
    return request.session.get('_server_id') != _server_instance_id


def _clear_stale_session(request):
    """Wipe simulation state when session is from before server restart."""
    for key in ('simulation_hour', 'predictions', 'similar_patients', '_server_id'):
        if key in request.session:
            del request.session[key]
    request.session.modified = True


def _ensure_session_fresh(request):
    """If session is stale (server restarted), clear it and mark fresh."""
    if _is_session_stale(request):
        _clear_stale_session(request)
        request.session['_server_id'] = _server_instance_id
        request.session.modified = True


def _prediction_cache_key(subject_id, stay_id, hadm_id, hour):
    """Serializable key for session predictions dict."""
    return f"{subject_id}_{stay_id}_{hadm_id}_{hour}"


def _get_simulation_hour(request):
    """Get current simulation hour from session (default -1 = not started)."""
    _ensure_session_fresh(request)
    return request.session.get('simulation_hour', -1)


def _set_simulation_hour(request, hour):
    """Store simulation hour in session."""
    _ensure_session_fresh(request)
    request.session['simulation_hour'] = hour
    request.session['_server_id'] = _server_instance_id
    request.session.modified = True


def _get_prediction_cached(session, subject_id, stay_id, hadm_id, hour):
    """
    Read prediction from session cache. Returns dict with risk_score, comorbidity_group
    or None if not cached.
    """
    preds = session.get('predictions') or {}
    key = _prediction_cache_key(subject_id, stay_id, hadm_id, hour)
    return preds.get(key)


def _store_prediction(session, subject_id, stay_id, hadm_id, hour, risk_score, comorbidity_group):
    """Store prediction in session cache."""
    preds = session.get('predictions') or {}
    key = _prediction_cache_key(subject_id, stay_id, hadm_id, hour)
    preds[key] = {
        'risk_score': risk_score,
        'comorbidity_group': comorbidity_group,
    }
    session['predictions'] = preds
    session.modified = True


def _get_similar_patients_cached(session, subject_id, stay_id, hadm_id, hour):
    """Read similar patients from session cache. Returns list or None if not cached."""
    cache = session.get('similar_patients') or {}
    key = _prediction_cache_key(subject_id, stay_id, hadm_id, hour)
    return cache.get(key)


def _store_similar_patients(session, subject_id, stay_id, hadm_id, hour, similar_list):
    """Store similar patients in session cache (avoids re-loading CSV on every page load)."""
    cache = session.get('similar_patients') or {}
    key = _prediction_cache_key(subject_id, stay_id, hadm_id, hour)
    cache[key] = similar_list
    session['similar_patients'] = cache
    session.modified = True


# =============================================================================
# Helper functions
# =============================================================================

def _display_time(current_hour):
    """
    Frontend display time — offset by +1 so the first click shows 01:00
    instead of staying at 00:00.  The backend data queries still use
    current_hour directly (0, 1, 2, …).
    """
    display_hour = current_hour + 1
    if display_hour <= 0:
        return "00:00"
    elif display_hour >= 24:
        return "00:00"
    else:
        return f"{display_hour:02d}:00"


def _time_since_admission(intime, current_hour):
    """
    Compute time since admission using TIME only (no datetime).
    March 13 is display-only; cohort is pre-selected. Subtract admission time
    from simulation clock time. Returns (display_string, total_minutes).
    """
    if current_hour < 0 or intime is None:
        return ("-", -1)
    # Simulation clock: (current_hour + 1):00 (matches _display_time)
    sim_minutes = (current_hour + 1) * 60 if current_hour < 23 else 24 * 60
    adm_minutes = intime.hour * 60 + intime.minute
    delta = sim_minutes - adm_minutes
    if delta < 0:
        delta += 24 * 60  # Overnight (e.g. admitted 22:00, sim 06:00)
    hours = delta // 60
    minutes = delta % 60
    return (f"{hours:02d}:{minutes:02d}", delta)


def _prediction_as_of_dt(current_hour, patient_intime=None):
    """
    Backend timestamp used for model scoring per simulation hour.
    
    Always returns normalized 2025-03-13 timestamps for consistency,
    even though the database contains patients from various years.
    The actual DB queries are year-agnostic (filter by month/day only).
    
    Args:
        current_hour: Hour of simulation (0-23)
        patient_intime: Not used anymore (kept for API compatibility)
    """
    if current_hour < 0:
        return None
    
    # Always use normalized 2025-03-13 for predictions
    year, month, day = 2025, 3, 13
    
    if current_hour >= 23:
        return datetime(2025, 3, 14, 0, 0, 0)
    return datetime(year, month, day, current_hour + 1, 0, 0)


def _get_cohort_patients():
    """
    Get the base queryset of cohort patients.
    No date filter: cohort may include patients from any admission date.
    """
    patients = UniquePatientProfile.objects.all()

    cohort = get_cohort_filter()
    if cohort:
        if cohort['type'] == 'subject_ids':
            patients = patients.filter(subject_id__in=cohort['values'])
        elif cohort['type'] == 'tuples':
            conditions = Q()
            for subject_id, stay_id, hadm_id in cohort['values']:
                conditions |= Q(subject_id=subject_id, stay_id=stay_id, hadm_id=hadm_id)
            patients = patients.filter(conditions)

    return patients


def _get_admitted_patients(current_hour):
    """
    Get patients whose admission hour-of-day is <= current_hour.
    Works for any admission date: we use intime.hour (0-23) as simulation arrival hour.
    Returns an empty queryset if simulation hasn't started (hour < 0).
    """
    if current_hour < 0:
        return UniquePatientProfile.objects.none()

    return _get_cohort_patients().filter(intime__hour__lte=current_hour)


def _patient_charttime_range(patient_intime, current_hour):
    """
    Map simulation hour to patient's actual charttime_hour range.
    Returns (start_hour, end_hour) for filtering vitals/procedures.
    """
    if not patient_intime or current_hour < 0:
        return None, None
    base = patient_intime.replace(minute=0, second=0, microsecond=0)
    adm_hour = patient_intime.hour
    if current_hour < adm_hour:
        return None, None
    hours_since_adm = current_hour - adm_hour
    start_hour = base
    end_hour = base + timedelta(hours=hours_since_adm)
    return start_hour, end_hour


def _patient_as_of_dt(patient_intime, current_hour):
    """
    Map simulation hour to patient's actual as_of timestamp for predictions.
    Returns end of current hour in patient's timeline.
    """
    if not patient_intime or current_hour < 0:
        return None
    base = patient_intime.replace(minute=0, second=0, microsecond=0)
    adm_hour = patient_intime.hour
    if current_hour < adm_hour:
        return None
    # End of hour (current_hour - adm_hour) of stay
    return base + timedelta(hours=current_hour - adm_hour + 1)


# =============================================================================
# Views
# =============================================================================

def patient_list(request):
    """
    Display a list of all patients currently admitted in the simulation.
    URL: /patients/
    Predictions are read from session cache (populated only when +1 is clicked).
    """
    current_hour = _get_simulation_hour(request)
    patients = _get_admitted_patients(current_hour)

    # Attach display names and predictions from session cache
    name_mapping = get_display_name_mapping()
    
    patients_list = []
    for p in patients:
        p.display_name = name_mapping.get(
            (p.subject_id, p.stay_id, p.hadm_id),
            f"Patient {p.subject_id}"
        )
        
        # Read prediction from session cache (no model call on page load)
        if current_hour >= 0:
            cached = _get_prediction_cached(
                request.session, p.subject_id, p.stay_id, p.hadm_id, current_hour
            )
            if cached:
                rs = cached.get('risk_score')
                p.risk_score = (rs * 100) if rs is not None else None
                p.comorbidity_group = cached.get('comorbidity_group')
            else:
                p.risk_score = None
                p.comorbidity_group = None
        else:
            p.risk_score = None
            p.comorbidity_group = None

        p.time_since_admission, p.time_since_minutes = _time_since_admission(p.intime, current_hour)
        patients_list.append(p)

    context = {
        'patients': patients_list,
        'total_patients': len(patients_list),
        'cohort_active': get_cohort_filter() is not None,
        'current_hour': current_hour,
        'current_time_display': _display_time(current_hour),
    }
    return render(request, 'patients/index.html', context)


def patient_detail(request, subject_id, stay_id, hadm_id):
    """
    Display details for a specific patient stay, including vitalsign chart
    and procedure events log up to the current simulation hour.

    URL: /patients/<subject_id>/<stay_id>/<hadm_id>/
    """
    patient = get_object_or_404(
        UniquePatientProfile,
        subject_id=subject_id,
        stay_id=stay_id,
        hadm_id=hadm_id
    )

    current_hour = _get_simulation_hour(request)
    vitalsigns_json = '[]'
    chemistry_json = '[]'
    coagulation_json = '[]'
    procedures = []

    if current_hour >= 0:
        # --- Vitalsigns for Plotly chart ---
        # Use patient's actual intime to map simulation hour -> charttime range
        start_hour, end_hour = _patient_charttime_range(patient.intime, current_hour)
        if start_hour is not None and end_hour is not None:
            vitalsigns_qs = VitalsignHourly.objects.filter(
                subject_id=subject_id,
                stay_id=stay_id,
                charttime_hour__gte=start_hour,
                charttime_hour__lte=end_hour,
            ).order_by('charttime_hour')
        else:
            vitalsigns_qs = VitalsignHourly.objects.none()

        vitalsigns_list = []
        for row in vitalsigns_qs.values(
            'charttime_hour',
            'heart_rate', 'sbp', 'dbp', 'mbp',
            'resp_rate', 'temperature', 'spo2', 'glucose',
        ):
            # Add a clean hour label for the Plotly x-axis
            row['hour_label'] = f"{row['charttime_hour'].hour:02d}:00"
            vitalsigns_list.append(row)

        vitalsigns_json = json.dumps(vitalsigns_list, cls=DjangoJSONEncoder)

        # --- Chemistry for Plotly chart ---
        if start_hour is not None and end_hour is not None:
            chemistry_qs = ChemistryHourly.objects.filter(
                subject_id=subject_id,
                stay_id=stay_id,
                charttime_hour__gte=start_hour,
                charttime_hour__lte=end_hour,
            ).order_by('charttime_hour')
        else:
            chemistry_qs = ChemistryHourly.objects.none()

        chemistry_list = []
        for row in chemistry_qs.values(
            'charttime_hour',
            'bicarbonate', 'calcium', 'sodium', 'potassium',
        ):
            # Add a clean hour label for the Plotly x-axis
            row['hour_label'] = f"{row['charttime_hour'].hour:02d}:00"
            chemistry_list.append(row)

        chemistry_json = json.dumps(chemistry_list, cls=DjangoJSONEncoder)

        # --- Coagulation for Plotly chart ---
        if start_hour is not None and end_hour is not None:
            coagulation_qs = CoagulationHourly.objects.filter(
                subject_id=subject_id,
                stay_id=stay_id,
                charttime_hour__gte=start_hour,
                charttime_hour__lte=end_hour,
            ).order_by('charttime_hour')
        else:
            coagulation_qs = CoagulationHourly.objects.none()

        coagulation_list = []
        for row in coagulation_qs.values(
            'charttime_hour',
            'd_dimer', 'fibrinogen', 'thrombin', 'inr', 'pt', 'ptt',
        ):
            # Add a clean hour label for the Plotly x-axis
            row['hour_label'] = f"{row['charttime_hour'].hour:02d}:00"
            coagulation_list.append(row)

        coagulation_json = json.dumps(coagulation_list, cls=DjangoJSONEncoder)

        # --- Procedure events for the log ---
        if start_hour is not None and end_hour is not None:
            procedures = list(ProcedureeventsHourly.objects.filter(
                subject_id=subject_id,
                stay_id=stay_id,
                charttime_hour__gte=start_hour,
                charttime_hour__lte=end_hour,
                itemid__isnull=False,
            ).order_by('charttime_hour').values(
            'charttime_hour', 'charttime',
            'item_label', 'value', 'valueuom',
            'ordercategoryname', 'statusdescription',
        ))
        else:
            procedures = []

        procedures = procedures[::-1]
        
        # Count non-empty procedures (those with item_label)
        procedures_non_empty_count = sum(1 for p in procedures if p.get('item_label'))
    else:
        procedures_non_empty_count = 0

    # Prediction "as_of" time for the API (normalized to 2025-03-13)
    # Database queries are year-agnostic, display is normalized for consistency
    if current_hour < 0:
        prediction_as_of_iso = None
    elif current_hour >= 23:
        prediction_as_of_iso = "2025-03-14T00:00:00"
    else:
        prediction_as_of_iso = f"2025-03-13T{current_hour + 1:02d}:00:00"

    # Attach display name (IDs hidden from clinicians)
    name_mapping = get_display_name_mapping()
    patient.display_name = name_mapping.get(
        (patient.subject_id, patient.stay_id, patient.hadm_id),
        f"Patient {patient.subject_id}"
    )
    patient.time_since_admission, _ = _time_since_admission(patient.intime, current_hour)

    # Read prediction from session cache (no model call on page load)
    patient.risk_score = None
    if current_hour >= 0:
        cached = _get_prediction_cached(
            request.session, patient.subject_id, patient.stay_id, patient.hadm_id, current_hour
        )
        if cached:
            rs = cached.get('risk_score')
            patient.risk_score = (rs * 100) if rs is not None else None

    context = {
        'patient': patient,
        'vitalsigns_json': vitalsigns_json,
        'chemistry_json': chemistry_json,
        'coagulation_json': coagulation_json,
        'procedures': procedures,
        'procedures_count': len(procedures),
        'procedures_non_empty_count': procedures_non_empty_count,
        'current_hour': current_hour,
        'current_time_display': _display_time(current_hour),
        'prediction_as_of_iso': prediction_as_of_iso,
    }
    return render(request, 'patients/show.html', context)


@require_POST
def advance_time(request):
    """
    API endpoint: advance the simulation clock by 1 hour.

    Returns JSON with:
      - current_hour
      - new_patients admitted at this hour
      - vitalsigns for ALL admitted patients at this hour (may be empty)
      - procedureevents for ALL admitted patients at this hour (may be empty)

    POST /patients/advance-time/
    """
    import traceback
    try:
        return _advance_time_impl(request)
    except Exception as e:
        return JsonResponse({
            'error': str(e),
            'traceback': traceback.format_exc(),
        }, status=500)


def _advance_time_impl(request):
    """Implementation of advance_time (wrapped for error handling)."""
    # --- Advance the clock (session-based) ---
    current_hour = _get_simulation_hour(request) + 1
    _set_simulation_hour(request, current_hour)

    # Cap at hour 23
    if current_hour > 23:
        _set_simulation_hour(request, 23)
        return JsonResponse({
            'error': 'Cannot advance past 23:00',
            'current_hour': 23,
            'current_time': _display_time(23),
        }, status=400)

    # --- 1. New patients admitted at this exact hour ---
    new_patients_qs = _get_cohort_patients().filter(intime__hour=current_hour)

    new_patients_data = list(new_patients_qs.values(
        'subject_id', 'stay_id', 'hadm_id',
        'anchor_age', 'gender', 'race',
        'first_careunit', 'intime', 'outtime', 'los',
    ))

    # --- 2. All currently admitted patients (with intime for charttime mapping) ---
    admitted_qs = _get_admitted_patients(current_hour)
    admitted_patients = list(admitted_qs.values('subject_id', 'stay_id', 'hadm_id', 'intime'))
    admitted_stay_ids = [p['stay_id'] for p in admitted_patients]

    # --- 3. Vitalsigns at this hour for admitted patients ---
    # Map simulation hour -> each patient's actual charttime_hour
    vitalsigns_data = []
    if admitted_patients:
        vitals_conditions = Q()
        for p in admitted_patients:
            intime = p.get('intime')
            if intime and intime.hour <= current_hour:
                target = intime.replace(minute=0, second=0, microsecond=0) + timedelta(
                    hours=(current_hour - intime.hour)
                )
                vitals_conditions |= Q(stay_id=p['stay_id'], charttime_hour=target)
        if vitals_conditions:
            vitalsigns_qs = VitalsignHourly.objects.filter(vitals_conditions)
            vitalsigns_data = list(vitalsigns_qs.values(
            'subject_id', 'stay_id', 'charttime_hour',
            'heart_rate', 'sbp', 'dbp', 'mbp',
            'sbp_ni', 'dbp_ni', 'mbp_ni',
            'resp_rate', 'temperature', 'temperature_site',
            'spo2', 'glucose',
        ))

    # --- 4. Procedure events at this hour for admitted patients ---
    procedures_data = []
    if admitted_patients:
        proc_conditions = Q()
        for p in admitted_patients:
            intime = p.get('intime')
            if intime and intime.hour <= current_hour:
                target = intime.replace(minute=0, second=0, microsecond=0) + timedelta(
                    hours=(current_hour - intime.hour)
                )
                proc_conditions |= Q(stay_id=p['stay_id'], charttime_hour=target)
        if proc_conditions:
            procedures_qs = ProcedureeventsHourly.objects.filter(proc_conditions)
            procedures_data = list(procedures_qs.values(
                'subject_id', 'stay_id', 'charttime_hour', 'charttime',
                'itemid', 'item_label', 'item_unitname',
                'value', 'valueuom',
                'location', 'locationcategory',
                'ordercategoryname', 'ordercategorydescription',
                'statusdescription', 'originalamount', 'originalrate',
            ))
        else:
            procedures_data = []

    # --- 5. Score ALL admitted patients at this hour (model runs ONLY here) ---
    # Store results in session so page loads read from cache
    model_scoring_table = []
    display_as_of = _prediction_as_of_dt(current_hour)  # For response display
    for patient in admitted_patients:
        actual_as_of = _patient_as_of_dt(patient.get('intime'), current_hour)
        if actual_as_of is not None:
            pred = get_prediction(
                subject_id=patient['subject_id'],
                stay_id=patient['stay_id'],
                hadm_id=patient['hadm_id'],
                as_of=actual_as_of,
                window_hours=24,
            )
            if pred.get('ok'):
                risk_score = pred.get('risk_score')
                comorbidity_group = pred.get('comorbidity_group')
                _store_prediction(
                    request.session,
                    patient['subject_id'], patient['stay_id'], patient['hadm_id'],
                    current_hour,
                    risk_score, comorbidity_group,
                )
                model_scoring_table.append({
                    'subject_id': patient['subject_id'],
                    'stay_id': patient['stay_id'],
                    'hadm_id': patient['hadm_id'],
                    'as_of': display_as_of.isoformat() if display_as_of else None,
                    'risk_score': risk_score,
                    'comorbidity_group': comorbidity_group,
                    'ok': True,
                })
            else:
                model_scoring_table.append({
                    'subject_id': patient['subject_id'],
                    'stay_id': patient['stay_id'],
                    'hadm_id': patient['hadm_id'],
                    'as_of': display_as_of.isoformat() if display_as_of else None,
                    'ok': False,
                    'error': pred.get('error', 'prediction failed'),
                })

    # --- Build response ---
    response_data = {
        'current_hour': current_hour,
        'current_time': _display_time(current_hour),
        'new_patients': new_patients_data,
        'new_patients_count': len(new_patients_data),
        'total_admitted': len(admitted_stay_ids),
        'vitalsigns': vitalsigns_data,
        'vitalsigns_count': len(vitalsigns_data),
        'procedureevents': procedures_data,
        'procedureevents_count': len(procedures_data),
        'model_scoring_table': model_scoring_table,
        'model_scoring_count': len(model_scoring_table),
    }

    return JsonResponse(response_data)


def patient_prediction(request, subject_id, stay_id, hadm_id):
    """
    Display prediction details for a specific patient.
    URL: /patients/<subject_id>/<stay_id>/<hadm_id>/prediction-view/
    Predictions read from session cache (populated only when +1 is clicked).
    """
    patient = get_object_or_404(
        UniquePatientProfile,
        subject_id=subject_id,
        stay_id=stay_id,
        hadm_id=hadm_id
    )

    current_hour = _get_simulation_hour(request)
    as_of_dt = _prediction_as_of_dt(current_hour)

    # Read prediction from session cache (no model call on page load)
    risk_score = None
    comorbidity_group = None
    prediction_error = None

    if current_hour >= 0:
        cached = _get_prediction_cached(
            request.session, subject_id, stay_id, hadm_id, current_hour
        )
        if cached:
            risk_score = cached.get('risk_score')
            comorbidity_group = cached.get('comorbidity_group')
        else:
            prediction_error = 'No prediction yet. Advance time (+1) to compute.'

    # Similar patients: use cache if available; otherwise load async (see template)
    # CSV load + similarity is slow — we skip it here and let the client fetch via API
    similar_patients = []
    load_similar_async = False
    if as_of_dt:
        cached_similar = _get_similar_patients_cached(
            request.session, subject_id, stay_id, hadm_id, current_hour
        )
        if cached_similar is not None:
            similar_patients = cached_similar
        else:
            load_similar_async = True  # Client will fetch; page loads fast

    # Attach display name, time since admission, and risk score (for patient details)
    name_mapping = get_display_name_mapping()
    patient.display_name = name_mapping.get(
        (patient.subject_id, patient.stay_id, patient.hadm_id),
        f"Patient {patient.subject_id}"
    )
    patient.time_since_admission, _ = _time_since_admission(patient.intime, current_hour)
    patient.risk_score = (risk_score * 100) if risk_score is not None else None

    # Attach display names to similar patients (generate deterministic names for non-cohort patients)
    from .display_names import DISPLAY_NAMES
    for sp in similar_patients:
        # Try cohort mapping first, then generate deterministic name from DISPLAY_NAMES pool
        cohort_name = name_mapping.get((sp['subject_id'], sp['stay_id'], sp['hadm_id']))
        if cohort_name:
            sp['display_name'] = cohort_name
        else:
            # Use subject_id as seed for deterministic but unique name assignment
            name_idx = sp['subject_id'] % len(DISPLAY_NAMES)
            sp['display_name'] = DISPLAY_NAMES[name_idx]

    # Sepsis3 suspected_infection_time (use time only: hour + minute)
    suspected_infection_time = get_sepsis3_suspected_infection_time(subject_id, stay_id)

    # Predictions-by-hour for chart: read from session cache (no model calls)
    predictions_json = '[]'
    model_sepsis_hour_index = None
    actual_sepsis_x = None
    if current_hour >= 0:
        chart_hour = current_hour + 1
        predictions_list = []
        for h in range(chart_hour):
            as_of_h = _prediction_as_of_dt(h)
            if not as_of_h:
                continue
            cached = _get_prediction_cached(
                request.session, subject_id, stay_id, hadm_id, h
            )
            rs = cached.get('risk_score') if cached else None
            pct = (rs * 100) if rs is not None else None
            hour_label = f"{as_of_h.hour:02d}:{as_of_h.minute:02d}"
            predictions_list.append({"hour_label": hour_label, "hour_val": h + 1, "risk_score": pct})
            if model_sepsis_hour_index is None and pct is not None and pct >= 30:
                model_sepsis_hour_index = len(predictions_list) - 1

        if suspected_infection_time and hasattr(suspected_infection_time, 'hour'):
            si_hour = suspected_infection_time.hour
            si_minute = getattr(suspected_infection_time, 'minute', 0) or 0
            si_x = si_hour + si_minute / 60.0
            sim_x = chart_hour
            if si_x <= sim_x:
                actual_sepsis_x = si_x

        predictions_json = json.dumps(predictions_list, cls=DjangoJSONEncoder)

    predictions_meta_json = json.dumps({
        "model_sepsis_hour_index": model_sepsis_hour_index if current_hour >= 0 else None,
        "actual_sepsis_x": actual_sepsis_x if current_hour >= 0 else None,
    }, cls=DjangoJSONEncoder)

    # SOFA charts: fetch sofa_hourly for this patient up to current simulation hour
    sofa_24hours_json = '[]'
    sofa_other_json = '[]'
    if current_hour >= 0:
        start_hour, end_hour = _patient_charttime_range(patient.intime, current_hour)
        if start_hour is not None and end_hour is not None:
            sofa_qs = SofaHourly.objects.filter(
                subject_id=subject_id,
                stay_id=stay_id,
                charttime_hour__gte=start_hour,
                charttime_hour__lte=end_hour,
            ).order_by('charttime_hour')

            sofa_24_cols = [
                'respiration_24hours', 'coagulation_24hours', 'liver_24hours',
                'cardiovascular_24hours', 'cns_24hours', 'renal_24hours', 'sofa_24hours',
            ]
            sofa_other_cols = [
                'pao2fio2ratio_novent', 'pao2fio2ratio_vent',
                'rate_epinephrine', 'rate_norepinephrine', 'rate_dopamine', 'rate_dobutamine',
                'meanbp_min', 'gcs_min', 'uo_24hr',
                'bilirubin_max', 'creatinine_max', 'platelet_min',
                'respiration', 'coagulation', 'liver', 'cardiovascular', 'cns', 'renal',
            ]

            sofa_24_list = []
            sofa_other_list = []
            for row in sofa_qs.values('charttime_hour', *sofa_24_cols, *sofa_other_cols):
                r24 = {'hour_label': f"{row['charttime_hour'].hour:02d}:00"}
                for c in sofa_24_cols:
                    r24[c] = row[c]
                sofa_24_list.append(r24)

                ro = {'hour_label': f"{row['charttime_hour'].hour:02d}:00"}
                for c in sofa_other_cols:
                    ro[c] = row[c]
                sofa_other_list.append(ro)

            sofa_24hours_json = json.dumps(sofa_24_list, cls=DjangoJSONEncoder)
            sofa_other_json = json.dumps(sofa_other_list, cls=DjangoJSONEncoder)
            
    context = {
        'patient': patient,
        'risk_score': risk_score,
        'comorbidity_group': comorbidity_group,
        'prediction_error': prediction_error,
        'similar_patients': similar_patients,
        'load_similar_async': load_similar_async,
        'current_hour': current_hour,
        'current_time_display': _display_time(current_hour),
        'predictions_json': predictions_json,
        'predictions_meta_json': predictions_meta_json,
        'suspected_infection_time': suspected_infection_time,
        'sofa_24hours_json': sofa_24hours_json,
        'sofa_other_json': sofa_other_json,
        'prediction_as_of_iso': as_of_dt.isoformat() if as_of_dt else None,
    }
    return render(request, 'patients/prediction.html', context)
