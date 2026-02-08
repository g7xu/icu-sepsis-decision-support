"""
URL configuration for ICU Sepsis Decision Support System.
"""

from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('patients/', include('patients.urls')),
    path('', include('patients.urls')),  # Root URL goes to patients
]
