"""
Patient views - handles patient list, detail pages, and simulation clock API.
"""

import json
from datetime import datetime

from django.shortcuts import render, get_object_or_404
from django.core.paginator import Paginator
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .models import UniquePatientProfile, VitalsignHourly, ProcedureeventsHourly, ChemistryHourly, CoagulationHourly, SofaHourly
from .cohort import get_cohort_filter
from .services import get_prediction
from .display_names import get_display_name_mapping


# =============================================================================
# In-memory simulation state — resets when the server restarts
# =============================================================================
_simulation = {
    'current_hour': -1,  # -1 = not started yet (ICU is empty)
}


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
    return (f"{hours}:{minutes:02d}", delta)


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
    Get the base queryset of cohort patients admitted on March 13 (any year).
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

    # Only patients admitted on March 13 (ignore year)
    patients = patients.filter(intime__month=3, intime__day=13)
    return patients


def _get_admitted_patients(current_hour):
    """
    Get patients whose admission hour is <= current_hour on March 13.
    Returns an empty queryset if simulation hasn't started (hour < 0).
    """
    if current_hour < 0:
        return UniquePatientProfile.objects.none()

    return _get_cohort_patients().filter(intime__hour__lte=current_hour)


# =============================================================================
# Views
# =============================================================================

def patient_list(request):
    """
    Display a list of all patients currently admitted in the simulation.
    URL: /patients/
    """
    current_hour = _simulation['current_hour']
    patients = _get_admitted_patients(current_hour)

    # Attach display names and predictions first
    name_mapping = get_display_name_mapping()
    as_of_dt = _prediction_as_of_dt(current_hour)
    
    patients_list = []
    for p in patients:
        p.display_name = name_mapping.get(
            (p.subject_id, p.stay_id, p.hadm_id),
            f"Patient {p.subject_id}"
        )
        
        # Fetch prediction if simulation has started
        if as_of_dt:
            pred = get_prediction(
                subject_id=p.subject_id,
                stay_id=p.stay_id,
                hadm_id=p.hadm_id,
                as_of=as_of_dt,
                window_hours=24,
            )
            if pred.get('ok'):
                p.risk_score = pred.get('risk_score') * 100
                p.comorbidity_group = pred.get('comorbidity_group')
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

    current_hour = _simulation['current_hour']
    vitalsigns_json = '[]'
    chemistry_json = '[]'
    coagulation_json = '[]'
    procedures = []

    if current_hour >= 0:
        # --- Vitalsigns for Plotly chart ---
        vitalsigns_qs = VitalsignHourly.objects.filter(
            subject_id=subject_id,
            stay_id=stay_id,
            charttime_hour__month=3,
            charttime_hour__day=13,
            charttime_hour__hour__lte=current_hour,
        ).order_by('charttime_hour')

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
        chemistry_qs = ChemistryHourly.objects.filter(
            subject_id=subject_id,
            stay_id=stay_id,
            charttime_hour__month=3,
            charttime_hour__day=13,
            charttime_hour__hour__lte=current_hour,
        ).order_by('charttime_hour')

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
        coagulation_qs = CoagulationHourly.objects.filter(
            subject_id=subject_id,
            stay_id=stay_id,
            charttime_hour__month=3,
            charttime_hour__day=13,
            charttime_hour__hour__lte=current_hour,
        ).order_by('charttime_hour')

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
        procedures = list(ProcedureeventsHourly.objects.filter(
            subject_id=subject_id,
            stay_id=stay_id,
            charttime_hour__month=3,
            charttime_hour__day=13,
            charttime_hour__hour__lte=current_hour,
            itemid__isnull=False,
        ).order_by('charttime_hour').values(
            'charttime_hour', 'charttime',
            'item_label', 'value', 'valueuom',
            'ordercategoryname', 'statusdescription',
        ))

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

    # Fetch prediction for Sepsis Prediction link (same as index)
    patient.risk_score = None
    if prediction_as_of_iso:
        as_of_dt = _prediction_as_of_dt(current_hour)
        if as_of_dt:
            pred = get_prediction(
                patient.subject_id, patient.stay_id, patient.hadm_id,
                as_of=as_of_dt, window_hours=24,
            )
            if pred.get('ok'):
                patient.risk_score = pred.get('risk_score') * 100

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
    # --- Advance the clock ---
    _simulation['current_hour'] += 1
    current_hour = _simulation['current_hour']

    # Cap at hour 23
    if current_hour > 23:
        _simulation['current_hour'] = 23
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

    # --- 2. All currently admitted patient stay_ids ---
    admitted_qs = _get_admitted_patients(current_hour)
    admitted_stay_ids = list(admitted_qs.values_list('stay_id', flat=True))
    admitted_patients = list(admitted_qs.values('subject_id', 'stay_id', 'hadm_id'))

    # --- 3. Vitalsigns at this hour for admitted patients ---
    vitalsigns_data = []
    if admitted_stay_ids:
        vitalsigns_qs = VitalsignHourly.objects.filter(
            stay_id__in=admitted_stay_ids,
            charttime_hour__month=3,
            charttime_hour__day=13,
            charttime_hour__hour=current_hour,
        )
        vitalsigns_data = list(vitalsigns_qs.values(
            'subject_id', 'stay_id', 'charttime_hour',
            'heart_rate', 'sbp', 'dbp', 'mbp',
            'sbp_ni', 'dbp_ni', 'mbp_ni',
            'resp_rate', 'temperature', 'temperature_site',
            'spo2', 'glucose',
        ))

    # --- 4. Procedure events at this hour for admitted patients ---
    procedures_data = []
    if admitted_stay_ids:
        procedures_qs = ProcedureeventsHourly.objects.filter(
            stay_id__in=admitted_stay_ids,
            charttime_hour__month=3,
            charttime_hour__day=13,
            charttime_hour__hour=current_hour,
        )
        procedures_data = list(procedures_qs.values(
            'subject_id', 'stay_id', 'charttime_hour', 'charttime',
            'itemid', 'item_label', 'item_unitname',
            'value', 'valueuom',
            'location', 'locationcategory',
            'ordercategoryname', 'ordercategorydescription',
            'statusdescription', 'originalamount', 'originalrate',
        ))

    # --- 5. Score ALL admitted patients at this hour ---
    # Use normalized 2025-03-13 timestamp (DB queries are year-agnostic)
    model_scoring_table = []
    as_of_dt = _prediction_as_of_dt(current_hour)
    if as_of_dt is not None:
        for patient in admitted_patients:
            pred = get_prediction(
                subject_id=patient['subject_id'],
                stay_id=patient['stay_id'],
                hadm_id=patient['hadm_id'],
                as_of=as_of_dt,
                window_hours=24,
            )
            if pred.get('ok'):
                model_scoring_table.append({
                    'subject_id': patient['subject_id'],
                    'stay_id': patient['stay_id'],
                    'hadm_id': patient['hadm_id'],
                    'as_of': as_of_dt.isoformat(),
                    'risk_score': pred.get('risk_score'),
                    'comorbidity_group': pred.get('comorbidity_group'),
                    'ok': True,
                })
            else:
                model_scoring_table.append({
                    'subject_id': patient['subject_id'],
                    'stay_id': patient['stay_id'],
                    'hadm_id': patient['hadm_id'],
                    'as_of': as_of_dt.isoformat(),
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
    """
    patient = get_object_or_404(
        UniquePatientProfile,
        subject_id=subject_id,
        stay_id=stay_id,
        hadm_id=hadm_id
    )

    current_hour = _simulation['current_hour']
    as_of_dt = _prediction_as_of_dt(current_hour)

    # Fetch prediction
    risk_score = None
    comorbidity_group = None
    prediction_error = None

    if as_of_dt:
        pred = get_prediction(
            subject_id=subject_id,
            stay_id=stay_id,
            hadm_id=hadm_id,
            as_of=as_of_dt,
            window_hours=24,
        )
        if pred.get('ok'):
            risk_score = pred.get('risk_score')
            comorbidity_group = pred.get('comorbidity_group')
        else:
            prediction_error = pred.get('error', 'Prediction failed')

    # Attach display name and time since admission
    name_mapping = get_display_name_mapping()
    patient.display_name = name_mapping.get(
        (patient.subject_id, patient.stay_id, patient.hadm_id),
        f"Patient {patient.subject_id}"
    )
    patient.time_since_admission, _ = _time_since_admission(patient.intime, current_hour)

    # SOFA charts: fetch sofa_hourly for this patient up to current simulation hour
    sofa_24hours_json = '[]'
    sofa_other_json = '[]'
    if current_hour >= 0:
        sofa_qs = SofaHourly.objects.filter(
            subject_id=subject_id,
            stay_id=stay_id,
            charttime_hour__month=3,
            charttime_hour__day=13,
            charttime_hour__hour__lte=current_hour,
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
        'current_hour': current_hour,
        'current_time_display': _display_time(current_hour),
        'sofa_24hours_json': sofa_24hours_json,
        'sofa_other_json': sofa_other_json,
    }
    return render(request, 'patients/prediction.html', context)
