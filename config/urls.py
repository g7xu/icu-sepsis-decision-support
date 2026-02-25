"""
URL configuration for ICU Sepsis Decision Support System.
"""

from django.conf import settings
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('patients/', include('patients.urls')),
    path('', include('patients.urls')),  # Root URL goes to patients
]

if settings.DEMO_MODE:
    urlpatterns += [
        path('demo/patients/', include('patients.demo_urls')),
        path('demo/', include('patients.demo_urls')),
    ]
