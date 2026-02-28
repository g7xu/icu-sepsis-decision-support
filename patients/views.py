"""
Patient views - handles patient list, detail pages, and simulation clock API.

Demo date alignment: We display "March 13" for the simulation, but patients may be
from any admission date. Data queries use each patient's actual intime to map
simulation hour -> charttime_hour (hours since admission).
"""

import json
from datetime import datetime, timedelta

from django.shortcuts import render, get_object_or_404
from django.core.paginator import Paginator
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .models import UniquePatientProfile, VitalsignHourly, ProcedureeventsHourly, ChemistryHourly, CoagulationHourly
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
    Display a paginated list of patients currently admitted in the simulation.
    URL: /patients/
    """
    current_hour = _simulation['current_hour']
    patients = _get_admitted_patients(current_hour).order_by('subject_id')

    # Pagination - 25 patients per page
    paginator = Paginator(patients, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Attach display names (IDs hidden from clinicians)
    name_mapping = get_display_name_mapping()
    for p in page_obj:
        p.display_name = name_mapping.get(
            (p.subject_id, p.stay_id, p.hadm_id),
            f"Patient {p.subject_id}"
        )

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

    # --- 5. Score ALL admitted patients at this hour ---
    # Use patient-specific as_of (actual charttime) for DB queries; display stays March 13
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
                model_scoring_table.append({
                    'subject_id': patient['subject_id'],
                    'stay_id': patient['stay_id'],
                    'hadm_id': patient['hadm_id'],
                    'as_of': display_as_of.isoformat() if display_as_of else None,
                    'risk_score': pred.get('risk_score'),
                    'comorbidity_group': pred.get('comorbidity_group'),
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
