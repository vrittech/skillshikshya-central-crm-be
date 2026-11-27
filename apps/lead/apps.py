from django.apps import AppConfig


class LeadConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.lead'

    def ready(self):
        import apps.lead.signals  # noqa: F401
        import apps.lead.voice_notes  # noqa: F401
