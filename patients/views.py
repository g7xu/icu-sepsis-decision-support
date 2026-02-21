"""
Patient views — HTML pages and simulation clock API.

Simulation clock endpoints:
  POST /patients/advance-time/      step forward 1 hour
  POST /patients/rewind-time/       step backward 1 hour (true state rewind)
  POST /patients/play/              start auto-advance (forward or backward)
  POST /patients/pause/             stop auto-advance
  POST /patients/reset/             clear all sim tables, reset clock to -1
  GET  /patients/simulation-status/ polling endpoint for the frontend
"""

import json
from datetime import datetime

from django.core.paginator import Paginator
from django.core.serializers.json import DjangoJSONEncoder
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST

from .cohort import get_cohort_filter
from .models import (
    SimChemistryHourly,
    SimCoagulationHourly,
    SimPatient,
    SimProcedureeventsHourly,
    SimSofaHourly,
    SimVitalsignHourly,
)
from .pipeline import advance_hour, rewind_hour
from .services import get_prediction

# =============================================================================
# In-memory simulation state — resets when the server restarts
# =============================================================================

_simulation = {
    'current_hour': -1,      # -1 = not started (ICU empty)
    'auto_play': False,
    'speed_seconds': 5.0,    # real seconds per simulated hour
    'direction': 'forward',  # 'forward' | 'backward'
    '_thread': None,
}
_sim_lock = threading.Lock()


# =============================================================================
# Helper functions
# =============================================================================

def _display_time(current_hour: int) -> str:
    display_hour = current_hour + 1
    if display_hour <= 0:
        return "March 13, 2025 00:00"
    elif display_hour >= 24:
        return "March 14, 2025 00:00"
    return f"March 13, 2025 {display_hour:02d}:00"


def _prediction_as_of_dt(current_hour):
    """
    Backend timestamp used for model scoring per simulation hour.
    Mirrors frontend display mapping (+1 hour offset).
    """
    if current_hour < 0:
        return None
    if current_hour >= 23:
        return datetime(2025, 3, 14, 0, 0, 0)
    return datetime(2025, 3, 13, current_hour + 1, 0, 0)


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
# HTML views
# =============================================================================

def patient_list(request):
    """
    Display admitted patients (all rows in sim_patient = admitted so far).
    URL: /patients/
    """
    current_hour = _simulation['current_hour']

    if current_hour < 0:
        patients = SimPatient.objects.none()
    else:
        patients = SimPatient.objects.all().order_by('subject_id')

    paginator = Paginator(patients, 25)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'page_obj': page_obj,
        'total_patients': patients.count(),
        'cohort_active': get_cohort_filter() is not None,
        'current_hour': current_hour,
        'current_time_display': _display_time(current_hour),
        'auto_play': _simulation['auto_play'],
        'speed_seconds': _simulation['speed_seconds'],
        'direction': _simulation['direction'],
    }
    return render(request, 'patients/index.html', context)


def patient_detail(request, subject_id, stay_id, hadm_id):
    """
    Display details for a specific patient stay.
    URL: /patients/<subject_id>/<stay_id>/<hadm_id>/
    """
    patient = get_object_or_404(
        SimPatient,
        subject_id=subject_id,
        stay_id=stay_id,
        hadm_id=hadm_id,
    )

    current_hour = _simulation['current_hour']
    vitalsigns_json = '[]'
    procedures = []

    if current_hour >= 0:
        vitalsigns_qs = SimVitalsignHourly.objects.filter(
            subject_id=subject_id,
            stay_id=stay_id,
        ).order_by('charttime_hour')

        vitalsigns_list = []
        for row in vitalsigns_qs.values(
            'charttime_hour',
            'heart_rate', 'sbp', 'dbp', 'mbp',
            'resp_rate', 'temperature', 'spo2', 'glucose',
        ):
            row['hour_label'] = f"{row['charttime_hour'].hour:02d}:00"
            vitalsigns_list.append(row)

        vitalsigns_json = json.dumps(vitalsigns_list, cls=DjangoJSONEncoder)

        procedures = list(SimProcedureeventsHourly.objects.filter(
            subject_id=subject_id,
            stay_id=stay_id,
        ).order_by('charttime_hour').values(
            'charttime_hour', 'charttime',
            'item_label', 'value', 'valueuom',
            'ordercategoryname', 'statusdescription',
        ))

    if current_hour < 0:
        prediction_as_of_iso = None
    elif current_hour >= 23:
        prediction_as_of_iso = "2025-03-14T00:00:00"
    else:
        prediction_as_of_iso = f"2025-03-13T{current_hour + 1:02d}:00:00"

    context = {
        'patient': patient,
        'vitalsigns_json': vitalsigns_json,
        'procedures': procedures,
        'procedures_count': len(procedures),
        'current_hour': current_hour,
        'current_time_display': _display_time(current_hour),
        'prediction_as_of_iso': prediction_as_of_iso,
    }
    return render(request, 'patients/show.html', context)


# =============================================================================
# Simulation clock API endpoints
# =============================================================================

@require_POST
def advance_time(request):
    """Step the simulation forward by 1 hour."""
    with _sim_lock:
        if _simulation['current_hour'] >= 23:
            return JsonResponse({'error': 'Cannot advance past 23:00', 'current_hour': 23}, status=400)
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
