from django.apps import AppConfig


class AuthfitConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'AuthFit'

    def ready(self):
           import AuthFit.signals  # noqa: F401