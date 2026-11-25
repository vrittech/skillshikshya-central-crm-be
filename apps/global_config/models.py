"""Settings saved from the CRM's Settings page (Cloud storage, Email).

Each is one row (pk=1). While no row exists the CRM keeps using the values
from the server's .env file; once an admin saves the page, the row wins.
Secrets are stored encrypted (see secrets.py) and never sent back to the browser.
"""
from django.conf import settings
from django.db import models

from apps.global_config.secrets import decrypt, encrypt


class SavedSettings(models.Model):
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        """The saved row, or None when the page was never saved."""
        return cls.objects.filter(pk=1).select_related('updated_by').first()


class StorageSettings(SavedSettings):
    """Cloudflare R2: where voice notes and uploaded files are kept."""

    account_id = models.CharField(max_length=64, blank=True)
    access_key_id = models.CharField(max_length=128, blank=True)
    secret_access_key_encrypted = models.TextField(blank=True)
    bucket_name = models.CharField(max_length=63, blank=True)
    public_url = models.URLField(max_length=300, blank=True)

    class Meta:
        db_table = 'global_storage_settings'
        verbose_name = 'Cloud storage settings'
        verbose_name_plural = 'Cloud storage settings'

    @property
    def secret_access_key(self):
        return decrypt(self.secret_access_key_encrypted)

    @secret_access_key.setter
    def secret_access_key(self, value):
        self.secret_access_key_encrypted = encrypt(value)


class EmailSettings(SavedSettings):
    """SMTP server the CRM sends email through."""

    class Security(models.TextChoices):
        STARTTLS = 'starttls', 'STARTTLS'
        SSL = 'ssl', 'SSL'
        NONE = 'none', 'None'

    host = models.CharField(max_length=255, blank=True)
    port = models.PositiveIntegerField(default=587)
    security = models.CharField(max_length=10, choices=Security.choices, default=Security.STARTTLS)
    username = models.CharField(max_length=255, blank=True)
    password_encrypted = models.TextField(blank=True)
    from_email = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = 'global_email_settings'
        verbose_name = 'Email settings'
        verbose_name_plural = 'Email settings'

    @property
    def password(self):
        return decrypt(self.password_encrypted)

    @password.setter
    def password(self, value):
        self.password_encrypted = encrypt(value)
