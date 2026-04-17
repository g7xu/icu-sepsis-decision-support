"""
URL configuration for ICU Sepsis Decision Support System.
"""

from django.contrib import admin
from django.urls import path, include
from django.views.generic import TemplateView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('patients/', include('patients.urls')),
    path('poster/', TemplateView.as_view(template_name='poster.html'), name='poster'),
    path('', TemplateView.as_view(template_name='landing.html'), name='landing'),
]
