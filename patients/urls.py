"""
URL patterns for the patients app.
"""

from django.urls import path
from . import views
from . import api

app_name = 'patients'

urlpatterns = [
    # UI Views
    path('', views.patient_list, name='index'),
    path('<int:subject_id>/<int:stay_id>/<int:hadm_id>/', views.patient_detail, name='detail'),

    # Simulation clock controls
    path('advance-time/',      views.advance_time,      name='advance_time'),
    path('rewind-time/',       views.rewind_time,        name='rewind_time'),
    path('play/',              views.play,               name='play'),
    path('pause/',             views.pause,              name='pause'),
    path('reset/',             views.reset,              name='reset'),
    path('simulation-status/', views.simulation_status,  name='simulation_status'),

    # JSON API Endpoints (Features for ML)
    path('<int:subject_id>/<int:stay_id>/<int:hadm_id>/features/static', api.get_static_features, name='features_static'),
    path('<int:subject_id>/<int:stay_id>/<int:hadm_id>/features/hourly', api.get_hourly_features, name='features_hourly'),
    path('<int:subject_id>/<int:stay_id>/<int:hadm_id>/features/hourly-wide', api.get_hourly_wide_features, name='features_hourly_wide'),
    path('<int:subject_id>/<int:stay_id>/<int:hadm_id>/feature-bundle', api.get_feature_bundle, name='feature_bundle'),

    # Prediction (risk_score + comorbidity_group for patient view routing)
    path('<int:subject_id>/<int:stay_id>/<int:hadm_id>/prediction', api.get_prediction_view, name='prediction'),
]
