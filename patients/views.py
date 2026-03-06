"""
Production patient views — no simulation clock.

Shows all data currently in sim_* tables (populated by the pipeline).
The simulation dock is NOT shown on production views.
"""

import json

from django.shortcuts import render, get_object_or_404
from django.core.serializers.json import DjangoJSONEncoder
from django.utils.dateparse import parse_datetime

from .models import (
    SimPatient,
    SimVitalsignHourly,
    SimProcedureeventsHourly,
    SimChemistryHourly,
    SimCoagulationHourly,
    SimSofaHourly,
)
from .cohort import get_cohort_filter
from .services import get_prediction
from .utils import prediction_as_of_iso as _prediction_as_of_iso, get_display_name


def _format_time_since(intime, current_hour):
    """Compute human-readable time since admission.

    MIMIC-IV dates are shifted (e.g. year 2111), so we compare only the
    time-of-day portion against the simulation hour on the same calendar day.
    Returns (display_string, total_minutes).
    Display string includes admission time, e.g. "23:18 (admitted 00:42)".
    """
    if intime is None:
        return None, None

    # Simulation "now" is the end of current_hour, i.e. (current_hour+1):00
    display_hour = current_hour + 1
    sim_now_minutes = display_hour * 60  # minutes since midnight

    # Patient admission time-of-day in minutes since midnight
    intime_minutes = intime.hour * 60 + intime.minute

    total_minutes = sim_now_minutes - intime_minutes
    if total_minutes < 0:
        return "Not yet admitted", -1

    hours = total_minutes // 60
    minutes = total_minutes % 60
    admitted_time = f"{intime.hour:02d}:{intime.minute:02d}"
    return f"{hours}:{minutes:02d} (admitted {admitted_time})", total_minutes


def patient_list(request):
    """Display all admitted patients from sim_patient table."""
    current_hour = 23
    patients = list(SimPatient.objects.all().order_by('subject_id'))

    for p in patients:
        p.display_name = get_display_name(p.subject_id, p.stay_id, p.hadm_id)
        time_str, time_mins = _format_time_since(p.intime, current_hour)
        p.time_since_admission = time_str or "Not yet admitted"
        p.time_since_minutes = time_mins or -1

    context = {
        'patients': patients,
        'total_patients': len(patients),
        'cohort_active': get_cohort_filter() is not None,
        'current_hour': current_hour,
        'current_time_display': '',
        'prediction_as_of_iso': _prediction_as_of_iso(current_hour),
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
    patient.display_name = get_display_name(subject_id, stay_id, hadm_id)

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


def prediction_detail(request, subject_id, stay_id, hadm_id):
    """Temporary prediction detail page for a patient."""
    patient = get_object_or_404(
        SimPatient,
        subject_id=subject_id,
        stay_id=stay_id,
        hadm_id=hadm_id,
    )
    patient.display_name = get_display_name(subject_id, stay_id, hadm_id)

    current_hour = 23
    as_of_iso = _prediction_as_of_iso(current_hour)
    risk_score = None
    risk_score_display = "\u2014"
    risk_color = "#718096"
    latent_class = None

    if as_of_iso:
        as_of = parse_datetime(as_of_iso)
        result = get_prediction(
            subject_id=subject_id,
            stay_id=stay_id,
            hadm_id=hadm_id,
            as_of=as_of,
            window_hours=24,
        )
        if result.get("ok"):
            score = result.get("risk_score")
            if score is not None:
                risk_score = score
                pct = round(score * 100)
                risk_score_display = f"{pct}%"
                if score >= 0.6:
                    risk_color = "#e53e3e"
                elif score >= 0.3:
                    risk_color = "#dd6b20"
                else:
                    risk_color = "#38a169"
            latent_class = result.get("latent_class")

    context = {
        'patient': patient,
        'risk_score': risk_score,
        'risk_score_display': risk_score_display,
        'risk_color': risk_color,
        'latent_class': latent_class,
        'show_sim_dock': False,
    }
    return render(request, 'patients/prediction.html', context)
