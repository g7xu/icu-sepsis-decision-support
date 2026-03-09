"""
URL configuration for ICU Sepsis Decision Support System.
"""

from django.conf import settings
from django.urls import path, include

urlpatterns = [
    path('patients/', include('patients.urls')),
    path('', include('patients.urls')),  # Root URL goes to patients
]

if settings.DEMO_MODE:
    urlpatterns += [
        path('demo/patients/', include('patients.demo_urls')),
        path('demo/', include('patients.demo_urls')),
    ]
