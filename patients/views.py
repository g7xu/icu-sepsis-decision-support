"""
Patient views - handles patient list, detail pages, and simulation clock API.
"""

import json

from django.shortcuts import render, get_object_or_404
from django.core.paginator import Paginator
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .models import UniquePatientProfile, VitalsignHourly, ProcedureeventsHourly
from .cohort import get_cohort_filter


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
        return "March 13, 2025 00:00"
    elif display_hour >= 24:
        return "March 14, 2025 00:00"
    else:
        return f"March 13, 2025 {display_hour:02d}:00"


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
    Display a paginated list of patients currently admitted in the simulation.
    URL: /patients/
    """
    current_hour = _simulation['current_hour']
    patients = _get_admitted_patients(current_hour).order_by('subject_id')

    # Pagination - 25 patients per page
    paginator = Paginator(patients, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj': page_obj,
        'total_patients': patients.count(),
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

        # --- Procedure events for the log ---
        procedures = list(ProcedureeventsHourly.objects.filter(
            subject_id=subject_id,
            stay_id=stay_id,
            charttime_hour__month=3,
            charttime_hour__day=13,
            charttime_hour__hour__lte=current_hour,
        ).order_by('charttime_hour').values(
            'charttime_hour', 'charttime',
            'item_label', 'value', 'valueuom',
            'ordercategoryname', 'statusdescription',
        ))

    # Prediction "as_of" time for the API (matches simulation clock)
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
    }

    return JsonResponse(response_data)
