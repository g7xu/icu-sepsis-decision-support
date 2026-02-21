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
import threading
import time

from django.shortcuts import render, get_object_or_404
from django.core.paginator import Paginator
from django.core.serializers.json import DjangoJSONEncoder
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET

from .models import (
    SimPatient,
    SimVitalsignHourly,
    SimProcedureeventsHourly,
    SimChemistryHourly,
    SimCoagulationHourly,
    SimSofaHourly,
)
from .pipeline import advance_hour, rewind_hour
from .cohort import get_cohort_filter


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


def _auto_advance_loop() -> None:
    """Background thread for auto-play (forward or backward)."""
    while True:
        with _sim_lock:
            if not _simulation['auto_play']:
                break
            direction = _simulation['direction']
            speed = _simulation['speed_seconds']
            hour = _simulation['current_hour']

        time.sleep(speed)

        with _sim_lock:
            if not _simulation['auto_play']:
                break
            if direction == 'forward':
                if hour >= 23:
                    _simulation['auto_play'] = False
                    break
                _simulation['current_hour'] += 1
                next_hour = _simulation['current_hour']
            else:
                if hour < 0:
                    _simulation['auto_play'] = False
                    break
                next_hour = None  # rewind handled below

        # Run DB work outside the lock
        try:
            if direction == 'forward':
                advance_hour(next_hour)
            else:
                rewind_hour(hour)
                with _sim_lock:
                    _simulation['current_hour'] -= 1
        except Exception:
            with _sim_lock:
                if direction == 'forward':
                    _simulation['current_hour'] -= 1
                else:
                    _simulation['current_hour'] += 1
                _simulation['auto_play'] = False
            break


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

    try:
        result = advance_hour(current_hour)
    except Exception as exc:
        with _sim_lock:
            _simulation['current_hour'] -= 1
        return JsonResponse({'error': str(exc)}, status=503)

    return JsonResponse(result)


@require_POST
def rewind_time(request):
    """Step the simulation backward by 1 hour (deletes that hour's sim rows)."""
    with _sim_lock:
        if _simulation['current_hour'] < 0:
            return JsonResponse({'error': 'Already at the beginning', 'current_hour': -1}, status=400)
        hour_to_remove = _simulation['current_hour']
        _simulation['current_hour'] -= 1

    try:
        rewind_hour(hour_to_remove)
    except Exception as exc:
        with _sim_lock:
            _simulation['current_hour'] += 1
        return JsonResponse({'error': str(exc)}, status=503)

    return JsonResponse({
        'status': 'rewound',
        'current_hour': _simulation['current_hour'],
        'current_time': _display_time(_simulation['current_hour']),
    })


@require_POST
def play(request):
    """Start auto-advancing the simulation clock."""
    with _sim_lock:
        if _simulation['auto_play']:
            return JsonResponse({'status': 'already_playing',
                                 'current_hour': _simulation['current_hour']})
        speed = float(request.POST.get('speed_seconds', 5.0))
        direction = request.POST.get('direction', 'forward')
        _simulation['auto_play'] = True
        _simulation['speed_seconds'] = speed
        _simulation['direction'] = direction

    t = threading.Thread(target=_auto_advance_loop, daemon=True)
    with _sim_lock:
        _simulation['_thread'] = t
    t.start()

    return JsonResponse({
        'status': 'playing',
        'direction': direction,
        'speed_seconds': speed,
        'current_hour': _simulation['current_hour'],
    })


@require_POST
def pause(request):
    """Stop the auto-advance loop."""
    with _sim_lock:
        _simulation['auto_play'] = False

    return JsonResponse({
        'status': 'paused',
        'current_hour': _simulation['current_hour'],
        'current_time': _display_time(_simulation['current_hour']),
    })


@require_POST
def reset(request):
    """Stop the clock and delete all sim table rows."""
    with _sim_lock:
        _simulation['auto_play'] = False
        _simulation['current_hour'] = -1
        _simulation['direction'] = 'forward'

    SimPatient.objects.all().delete()
    SimVitalsignHourly.objects.all().delete()
    SimProcedureeventsHourly.objects.all().delete()
    SimChemistryHourly.objects.all().delete()
    SimCoagulationHourly.objects.all().delete()
    SimSofaHourly.objects.all().delete()

    return JsonResponse({'status': 'reset', 'current_hour': -1})


@require_GET
def simulation_status(request):
    """Polling endpoint — returns current simulation state."""
    with _sim_lock:
        current_hour = _simulation['current_hour']
        auto_play = _simulation['auto_play']
        speed_seconds = _simulation['speed_seconds']
        direction = _simulation['direction']

    return JsonResponse({
        'current_hour': current_hour,
        'current_time': _display_time(current_hour),
        'auto_play': auto_play,
        'speed_seconds': speed_seconds,
        'direction': direction,
    })
