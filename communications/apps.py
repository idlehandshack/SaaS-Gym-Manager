from django.apps import AppConfig


class CommunicationsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'communications'
    verbose_name = 'Platform Communications'

    def ready(self):
        # Wires up cache-invalidation signals — mirrors announcements/models.py's
        # own @receiver-in-models.py style, but kept in signals.py here so
        # models.py stays purely declarative.
        from . import signals  # noqa: F401
