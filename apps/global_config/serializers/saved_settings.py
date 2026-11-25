"""Settings page forms. Secrets are write-only: blank keeps what is in use now."""
import re

from rest_framework import serializers

from apps.global_config.integrations import valid_from_address
from apps.global_config.models import EmailSettings

ACCOUNT_ID = re.compile(r'^[0-9a-fA-F]{32}$')
BUCKET_NAME = re.compile(r'^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$')


class StorageSettingsSerializer(serializers.Serializer):
    account_id = serializers.CharField(max_length=64)
    access_key_id = serializers.CharField(max_length=128)
    secret_access_key = serializers.CharField(
        max_length=256, required=False, allow_blank=True, write_only=True, trim_whitespace=True,
    )
    bucket_name = serializers.CharField(max_length=63)
    public_url = serializers.URLField(max_length=300, required=False, allow_blank=True)

    def validate_account_id(self, value):
        value = value.strip()
        if not value:
            return value
        if not ACCOUNT_ID.match(value):
            raise serializers.ValidationError('Enter the 32-character account ID from Cloudflare.')
        return value.lower()

    def validate_access_key_id(self, value):
        return value.strip()

    def validate_bucket_name(self, value):
        value = value.strip()
        if not value:
            return value
        if not BUCKET_NAME.match(value):
            raise serializers.ValidationError(
                'Use 3–63 lowercase letters, numbers and hyphens.'
            )
        return value

    def validate_public_url(self, value):
        return value.strip().rstrip('/')


class EmailSettingsSerializer(serializers.Serializer):
    host = serializers.CharField(max_length=255)
    port = serializers.IntegerField(min_value=1, max_value=65535)
    security = serializers.ChoiceField(choices=EmailSettings.Security.choices)
    username = serializers.CharField(max_length=255, required=False, allow_blank=True)
    password = serializers.CharField(
        max_length=256, required=False, allow_blank=True, write_only=True, trim_whitespace=False,
    )
    from_email = serializers.CharField(max_length=255)

    def validate_host(self, value):
        value = value.strip()
        if value and (' ' in value or '/' in value):
            raise serializers.ValidationError('Enter a server name like smtp.gmail.com.')
        return value

    def validate_username(self, value):
        return value.strip()

    def validate_from_email(self, value):
        value = value.strip()
        if value and not valid_from_address(value):
            raise serializers.ValidationError(
                'Enter an email address, like noreply@skillshikshya.com or Skill Shikshya <noreply@skillshikshya.com>.'
            )
        return value


class EmailTestSerializer(EmailSettingsSerializer):
    """Test email: the form as it is now (saved or not) plus who to send it to."""

    to = serializers.EmailField()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ('host', 'port', 'security', 'from_email'):
            self.fields[name].required = False
        for name in ('host', 'from_email'):
            self.fields[name].allow_blank = True


class StorageTestSerializer(StorageSettingsSerializer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ('account_id', 'access_key_id', 'bucket_name'):
            self.fields[name].required = False
            self.fields[name].allow_blank = True
