"""
URL patterns for demo mode (per-session simulation clock, in-memory data).
Mounted at /demo/patients/ when DEMO_MODE=true.
"""

from django.urls import path
from . import demo_views

app_name = 'demo'

urlpatterns = [
    path('', demo_views.demo_patient_list, name='index'),
    path('<int:subject_id>/<int:stay_id>/<int:hadm_id>/', demo_views.demo_patient_detail, name='detail'),

    # Demo clock controls
    path('advance-time/', demo_views.demo_advance_time, name='advance_time'),
    path('rewind-time/', demo_views.demo_rewind_time, name='rewind_time'),
    path('play/', demo_views.demo_play, name='play'),
    path('pause/', demo_views.demo_pause, name='pause'),
    path('reset/', demo_views.demo_reset, name='reset'),
    path('simulation-status/', demo_views.demo_simulation_status, name='simulation_status'),
]
