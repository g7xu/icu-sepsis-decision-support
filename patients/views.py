"""
Production patient views — no simulation clock.

Shows all data currently in sim_* tables (populated by the pipeline).
The simulation dock is NOT shown on production views.
"""

import json

from django.shortcuts import render, get_object_or_404
from django.core.paginator import Paginator
from django.core.serializers.json import DjangoJSONEncoder

from .models import (
    SimPatient,
    SimVitalsignHourly,
    SimProcedureeventsHourly,
    SimChemistryHourly,
    SimCoagulationHourly,
    SimSofaHourly,
)
from .cohort import get_cohort_filter
from .utils import prediction_as_of_iso as _prediction_as_of_iso


def patient_list(request):
    """Display all admitted patients from sim_patient table."""
    patients = SimPatient.objects.all().order_by('subject_id')

    paginator = Paginator(patients, 25)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'page_obj': page_obj,
        'total_patients': patients.count(),
        'cohort_active': get_cohort_filter() is not None,
        'current_hour': 23,
        'current_time_display': '',
        'show_sim_dock': False,
    }
    return render(request, 'patients/index.html', context)


def patient_detail(request, subject_id, stay_id, hadm_id):
    """Display details for a specific patient stay — all available data."""
    patient = get_object_or_404(
        SimPatient,
        subject_id=subject_id,
        stay_id=stay_id,
        hadm_id=hadm_id,
    )

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

    sofa_list = list(SimSofaHourly.objects.filter(
        subject_id=subject_id,
        stay_id=stay_id,
    ).order_by('charttime_hour').values(
        'charttime_hour', 'sofa_24hours',
        'respiration', 'coagulation', 'liver',
        'cardiovascular', 'cns', 'renal',
    ))
    for row in sofa_list:
        row['hour_label'] = f"{row['charttime_hour'].hour:02d}:00"
    sofa_json = json.dumps(sofa_list, cls=DjangoJSONEncoder)

    chem_list = list(SimChemistryHourly.objects.filter(
        subject_id=subject_id,
        stay_id=stay_id,
    ).order_by('charttime_hour').values(
        'charttime_hour', 'bicarbonate', 'calcium', 'sodium', 'potassium',
    ))
    for row in chem_list:
        row['hour_label'] = f"{row['charttime_hour'].hour:02d}:00"
    chemistry_json = json.dumps(chem_list, cls=DjangoJSONEncoder)

    coag_list = list(SimCoagulationHourly.objects.filter(
        subject_id=subject_id,
        stay_id=stay_id,
    ).order_by('charttime_hour').values(
        'charttime_hour', 'inr', 'ptt', 'pt', 'fibrinogen',
    ))
    for row in coag_list:
        row['hour_label'] = f"{row['charttime_hour'].hour:02d}:00"
    coagulation_json = json.dumps(coag_list, cls=DjangoJSONEncoder)

    procedures = list(SimProcedureeventsHourly.objects.filter(
        subject_id=subject_id,
        stay_id=stay_id,
    ).order_by('charttime_hour').values(
        'charttime_hour', 'charttime',
        'item_label', 'value', 'valueuom',
        'ordercategoryname', 'statusdescription',
    ))

    context = {
        'patient': patient,
        'vitalsigns_json': vitalsigns_json,
        'sofa_json': sofa_json,
        'chemistry_json': chemistry_json,
        'coagulation_json': coagulation_json,
        'procedures': procedures,
        'procedures_count': len(procedures),
        'current_hour': 23,
        'current_time_display': '',
        'prediction_as_of_iso': _prediction_as_of_iso(23),
        'show_sim_dock': False,
    }
    return render(request, 'patients/show.html', context)
