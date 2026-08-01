"""
WSGI config for ICU Sepsis Decision Support System.
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

application = get_wsgi_application()

# Vercel's @vercel/python builder looks for a WSGI callable named `app`.
app = application
