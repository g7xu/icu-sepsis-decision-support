import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class PatientsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'patients'

    def ready(self):
        from django.conf import settings
        if settings.DEMO_MODE:
            try:
                from . import demo_cache
                demo_cache.load()
            except Exception as exc:
                logger.warning("[PatientsConfig] Failed to load demo cache: %s", exc)
