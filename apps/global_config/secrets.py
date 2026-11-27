"""Encrypt passwords and keys before they go into the database.

The key comes from SETTINGS_ENCRYPTION_KEY, or SECRET_KEY when that isn't set.
If the key changes, old values can't be read any more: decrypt() returns ''
and the settings page shows the secret as missing, so it can be entered again.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet():
    raw = getattr(settings, 'SETTINGS_ENCRYPTION_KEY', '') or settings.SECRET_KEY
    digest = hashlib.sha256(f'crm-saved-settings:{raw}'.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value):
    if not value:
        return ''
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token):
    if not token:
        return ''
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return ''

