"""
Demo views — per-session simulation clock backed by in-memory cache.

All data comes from demo_cache (no DB writes). Each visitor gets their own
clock state stored in request.session['sim_state'].
"""

import json

from django.shortcuts import render
from django.http import JsonResponse, Http404
from django.core.paginator import Paginator
from django.core.serializers.json import DjangoJSONEncoder
from django.views.decorators.http import require_POST, require_GET

from . import demo_cache
from .cohort import get_cohort_filter
from .utils import display_time as _display_time, prediction_as_of_iso as _prediction_as_of_iso, get_display_name


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

_DEFAULT_SIM_STATE = {
    'current_hour': -1,
    'auto_play': False,
    'speed_seconds': 5.0,
    'direction': 'forward',
}


def _get_sim_state(request) -> dict:
    state = request.session.get('sim_state')
    if not state:
        state = dict(_DEFAULT_SIM_STATE)
        request.session['sim_state'] = state
    return state


def _save_sim_state(request, state: dict):
    request.session['sim_state'] = state
    request.session.modified = True


# ---------------------------------------------------------------------------
# HTML views
# ---------------------------------------------------------------------------

def demo_patient_list(request):
    state = _get_sim_state(request)
    current_hour = state['current_hour']

    if current_hour < 0:
        patients = []
    else:
        patients = demo_cache.get_patients_admitted_up_to(current_hour)
        for p in patients:
            p['display_name'] = get_display_name(p['subject_id'], p['stay_id'], p['hadm_id'])

    paginator = Paginator(patients, 25)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'page_obj': page_obj,
        'total_patients': len(patients),
        'cohort_active': get_cohort_filter() is not None,
        'current_hour': current_hour,
        'current_time_display': _display_time(current_hour),
        'auto_play': state['auto_play'],
        'speed_seconds': state['speed_seconds'],
        'direction': state['direction'],
        'show_sim_dock': True,
        'demo_mode_view': True,
    }
    return render(request, 'patients/index.html', context)


def demo_patient_detail(request, subject_id, stay_id, hadm_id):
    patient = demo_cache.get_patient(subject_id, stay_id, hadm_id)
    if not patient:
        raise Http404("Patient not found")

    state = _get_sim_state(request)
    current_hour = state['current_hour']

    vitalsigns_json = '[]'
    sofa_json = '[]'
    chemistry_json = '[]'
    coagulation_json = '[]'
    procedures = []

    if current_hour >= 0:
        # Vitals
        vitals_rows = demo_cache.get_data_up_to(demo_cache.vitals, stay_id, current_hour)
        vitalsigns_list = []
        for row in vitals_rows:
            entry = {k: row.get(k) for k in [
                'charttime_hour', 'heart_rate', 'sbp', 'dbp', 'mbp',
                'resp_rate', 'temperature', 'spo2', 'glucose',
            ]}
            ct = entry['charttime_hour']
            entry['hour_label'] = f"{ct.hour:02d}:00" if hasattr(ct, 'hour') else str(ct)
            vitalsigns_list.append(entry)
        vitalsigns_json = json.dumps(vitalsigns_list, cls=DjangoJSONEncoder)

        # SOFA
        sofa_rows = demo_cache.get_data_up_to(demo_cache.sofa, stay_id, current_hour)
        sofa_list = []
        for row in sofa_rows:
            entry = {k: row.get(k) for k in [
                'charttime_hour', 'sofa_24hours',
                'respiration', 'coagulation', 'liver',
                'cardiovascular', 'cns', 'renal',
            ]}
            ct = entry['charttime_hour']
            entry['hour_label'] = f"{ct.hour:02d}:00" if hasattr(ct, 'hour') else str(ct)
            sofa_list.append(entry)
        sofa_json = json.dumps(sofa_list, cls=DjangoJSONEncoder)

        # Chemistry
        chem_rows = demo_cache.get_data_up_to(demo_cache.chemistry, stay_id, current_hour)
        chem_list = []
        for row in chem_rows:
            entry = {k: row.get(k) for k in [
                'charttime_hour', 'bicarbonate', 'calcium', 'sodium', 'potassium',
            ]}
            ct = entry['charttime_hour']
            entry['hour_label'] = f"{ct.hour:02d}:00" if hasattr(ct, 'hour') else str(ct)
            chem_list.append(entry)
        chemistry_json = json.dumps(chem_list, cls=DjangoJSONEncoder)

        # Coagulation
        coag_rows = demo_cache.get_data_up_to(demo_cache.coagulation, stay_id, current_hour)
        coag_list = []
        for row in coag_rows:
            entry = {k: row.get(k) for k in [
                'charttime_hour', 'inr', 'ptt', 'pt', 'fibrinogen',
            ]}
            ct = entry['charttime_hour']
            entry['hour_label'] = f"{ct.hour:02d}:00" if hasattr(ct, 'hour') else str(ct)
            coag_list.append(entry)
        coagulation_json = json.dumps(coag_list, cls=DjangoJSONEncoder)

        # Procedures
        proc_rows = demo_cache.get_data_up_to(demo_cache.procedures, stay_id, current_hour)
        procedures = [
            {k: row.get(k) for k in [
                'charttime_hour', 'charttime',
                'item_label', 'value', 'valueuom',
                'ordercategoryname', 'statusdescription',
            ]}
            for row in proc_rows
        ]

    # Build a patient-like object for the template
    patient['display_name'] = get_display_name(subject_id, stay_id, hadm_id)
    patient_obj = _PatientProxy(patient)

    context = {
        'patient': patient_obj,
        'vitalsigns_json': vitalsigns_json,
        'sofa_json': sofa_json,
        'chemistry_json': chemistry_json,
        'coagulation_json': coagulation_json,
        'procedures': procedures,
        'procedures_count': len(procedures),
        'current_hour': current_hour,
        'current_time_display': _display_time(current_hour),
        'prediction_as_of_iso': _prediction_as_of_iso(current_hour),
        'auto_play': state['auto_play'],
        'speed_seconds': state['speed_seconds'],
        'show_sim_dock': True,
        'demo_mode_view': True,
    }
    return render(request, 'patients/show.html', context)


class _PatientProxy:
    """Lightweight wrapper so template attribute access works on a plain dict."""
    def __init__(self, d: dict):
        self.__dict__.update(d)


# ---------------------------------------------------------------------------
# Demo clock API (session-based, no DB writes)
# ---------------------------------------------------------------------------

@require_POST
def demo_advance_time(request):
    state = _get_sim_state(request)
    if state['current_hour'] >= 23:
        return JsonResponse({'error': 'Cannot advance past 23:00', 'current_hour': 23}, status=400)
    state['current_hour'] += 1
    _save_sim_state(request, state)
    return JsonResponse({
        'status': 'advanced',
        'current_hour': state['current_hour'],
        'current_time': _display_time(state['current_hour']),
    })


@require_POST
def demo_rewind_time(request):
    state = _get_sim_state(request)
    if state['current_hour'] < 0:
        return JsonResponse({'error': 'Already at the beginning', 'current_hour': -1}, status=400)
    state['current_hour'] -= 1
    _save_sim_state(request, state)
    return JsonResponse({
        'status': 'rewound',
        'current_hour': state['current_hour'],
        'current_time': _display_time(state['current_hour']),
    })


@require_POST
def demo_play(request):
    state = _get_sim_state(request)
    speed = float(request.POST.get('speed_seconds') or 5.0)
    direction = request.POST.get('direction', 'forward')
    state['auto_play'] = True
    state['speed_seconds'] = speed
    state['direction'] = direction
    _save_sim_state(request, state)
    return JsonResponse({
        'status': 'playing',
        'direction': direction,
        'speed_seconds': speed,
        'current_hour': state['current_hour'],
    })


@require_POST
def demo_pause(request):
    state = _get_sim_state(request)
    state['auto_play'] = False
    _save_sim_state(request, state)
    return JsonResponse({
        'status': 'paused',
        'current_hour': state['current_hour'],
        'current_time': _display_time(state['current_hour']),
    })


@require_POST
def demo_reset(request):
    state = dict(_DEFAULT_SIM_STATE)
    _save_sim_state(request, state)
    return JsonResponse({'status': 'reset', 'current_hour': -1})


@require_GET
def demo_simulation_status(request):
    state = _get_sim_state(request)
    return JsonResponse({
        'current_hour': state['current_hour'],
        'current_time': _display_time(state['current_hour']),
        'auto_play': state['auto_play'],
        'speed_seconds': state['speed_seconds'],
        'direction': state['direction'],
    })
