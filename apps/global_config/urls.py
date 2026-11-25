from django.urls import path

from apps.global_config.settings_views import (
    email_settings, storage_settings, test_email, test_storage,
)

urlpatterns = [
    path('settings/storage/', storage_settings, name='settings-storage'),
    path('settings/storage/test/', test_storage, name='settings-storage-test'),
    path('settings/email/', email_settings, name='settings-email'),
    path('settings/email/test/', test_email, name='settings-email-test'),
]
