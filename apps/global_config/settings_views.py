"""Settings page: Cloud storage (R2) and Email (SMTP). Admins only.

GET shows the values in use now (saved on this page, else from .env). Secrets
are never sent back, only whether one is set. PUT saves the whole form; a blank
secret keeps the one in use. The test endpoints use the form as sent, without
saving it, so a change can be tried before it goes live.
"""
from dataclasses import replace

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from apps.global_config.integrations import (
    email_config, r2_config, run_r2_test, send_test_email,
)
from apps.global_config.models import EmailSettings, StorageSettings
from apps.global_config.serializers.saved_settings import (
    EmailSettingsSerializer, EmailTestSerializer, StorageSettingsSerializer,
    StorageTestSerializer,
)


def _person(user):
    return (getattr(user, 'name', '') or getattr(user, 'email', '') or '') if user else ''


def _saved_meta(model):
    row = model.load()
    if row is None:
        return {'updated_at': None, 'updated_by_name': ''}
    return {'updated_at': row.updated_at, 'updated_by_name': _person(row.updated_by)}


def _storage_payload():
    cfg = r2_config()
    return {
        'source': cfg.source,
        'ready': cfg.ready,
        'account_id': cfg.account_id,
        'access_key_id': cfg.access_key_id,
        'secret_access_key_set': bool(cfg.secret_access_key),
        'bucket_name': cfg.bucket_name,
        'public_url': cfg.public_url,
        **_saved_meta(StorageSettings),
    }


def _email_payload(request):
    cfg = email_config()
    return {
        'source': cfg.source,
        'ready': cfg.ready,
        'host': cfg.host,
        'port': cfg.port,
        'security': cfg.security,
        'username': cfg.username,
        'password_set': bool(cfg.password),
        'from_email': cfg.from_email,
        'test_to': request.user.email,
        **_saved_meta(EmailSettings),
    }


@api_view(['GET', 'PUT'])
@permission_classes([IsAdminUser])
def storage_settings(request):
    if request.method == 'GET':
        return Response(_storage_payload())

    serializer = StorageSettingsSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    secret = data.get('secret_access_key') or r2_config().secret_access_key
    if not secret:
        return Response(
            {'secret_access_key': ['Enter the secret access key.']},
            status=status.HTTP_400_BAD_REQUEST,
        )
    row = StorageSettings.load() or StorageSettings()
    row.account_id = data['account_id']
    row.access_key_id = data['access_key_id']
    row.secret_access_key = secret
    row.bucket_name = data['bucket_name']
    row.public_url = data.get('public_url', '')
    row.updated_by = request.user
    row.save()
    return Response(_storage_payload())


@api_view(['POST'])
@permission_classes([IsAdminUser])
def test_storage(request):
    serializer = StorageTestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    current = r2_config()
    cfg = replace(
        current,
        account_id=data.get('account_id') or current.account_id,
        access_key_id=data.get('access_key_id') or current.access_key_id,
        secret_access_key=data.get('secret_access_key') or current.secret_access_key,
        bucket_name=data.get('bucket_name') or current.bucket_name,
    )
    if not cfg.ready:
        return Response(
            {'detail': 'Fill in the account ID, access key ID, secret access key and bucket name.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return Response(run_r2_test(cfg))


@api_view(['GET', 'PUT'])
@permission_classes([IsAdminUser])
def email_settings(request):
    if request.method == 'GET':
        return Response(_email_payload(request))

    serializer = EmailSettingsSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    username = data.get('username', '')
    password = (data.get('password') or email_config().password) if username else ''
    if username and not password:
        return Response({'password': ['Enter the password.']}, status=status.HTTP_400_BAD_REQUEST)
    row = EmailSettings.load() or EmailSettings()
    row.host = data['host']
    row.port = data['port']
    row.security = data['security']
    row.username = username
    row.password = password
    row.from_email = data['from_email']
    row.updated_by = request.user
    row.save()
    return Response(_email_payload(request))


@api_view(['POST'])
@permission_classes([IsAdminUser])
def test_email(request):
    serializer = EmailTestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    current = email_config()
    cfg = replace(
        current,
        host=data.get('host') or current.host,
        port=data.get('port') or current.port,
        security=data.get('security') or current.security,
        username=data['username'] if 'username' in data else current.username,
        password=data.get('password') or current.password,
        from_email=data.get('from_email') or current.from_email,
    )
    if not cfg.host:
        return Response({'detail': 'Fill in the SMTP server.'}, status=status.HTTP_400_BAD_REQUEST)
    return Response(send_test_email(cfg, data['to'], sent_by=_person(request.user)))
