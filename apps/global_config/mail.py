"""Email backend that sends through the SMTP server saved on the Settings page.

settings.py swaps the plain SMTP backend for this one. With nothing saved it
behaves exactly like Django's SMTP backend (values from .env). The console
and test backends are left alone, so local runs can still avoid real email.
"""
from django.conf import settings
from django.core.mail.backends.smtp import EmailBackend as SMTPBackend


class SavedSettingsEmailBackend(SMTPBackend):
    def __init__(self, host=None, port=None, username=None, password=None,
                 use_tls=None, fail_silently=False, use_ssl=None, timeout=None, **kwargs):
        from apps.global_config.integrations import email_config

        self.saved_from_email = ''
        cfg = email_config()
        if cfg.source == 'saved' and host is None:
            host, port = cfg.host, cfg.port
            username, password = cfg.username, cfg.password
            use_tls = cfg.security == 'starttls'
            use_ssl = cfg.security == 'ssl'
            self.saved_from_email = cfg.from_email
        super().__init__(
            host=host, port=port, username=username, password=password, use_tls=use_tls,
            fail_silently=fail_silently, use_ssl=use_ssl, timeout=timeout or 30, **kwargs,
        )

    def use_saved_from(self, email_messages):
        """Mail sent from the default address goes out from the saved From address."""
        if not self.saved_from_email:
            return
        for message in email_messages:
            if message.from_email == settings.DEFAULT_FROM_EMAIL:
                message.from_email = self.saved_from_email

    def send_messages(self, email_messages):
        self.use_saved_from(email_messages)
        return super().send_messages(email_messages)
