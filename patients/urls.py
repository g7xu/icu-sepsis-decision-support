"""
URL patterns for the patients app.
"""

from django.urls import path
from . import views

app_name = 'patients'

urlpatterns = [
    path('', views.patient_list, name='index'),
    path('<int:subject_id>/<int:stay_id>/<int:hadm_id>/', views.patient_detail, name='detail'),
]
