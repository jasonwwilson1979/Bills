from django.apps import AppConfig


class TrackerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tracker'

    def ready(self):
        # Connects the login receiver (pending share invites + admin access)
        from . import signals  # noqa: F401
