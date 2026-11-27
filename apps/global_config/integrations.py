"""Which R2 and SMTP values are in use, and the checks behind the Test buttons.

r2_config() / email_config() return the saved Settings page values when the
page was saved, else the server's .env values. Everything that talks to R2 or
sends email goes through these, so a change on the page applies at once,
in the web app and in the Celery worker, with no restart.
"""
import smtplib
import socket
import ssl
import time
import uuid
from dataclasses import dataclass, replace
from email.utils import parseaddr

import boto3
from botocore.config import Config
from botocore.exceptions import (
    ClientError, ConnectTimeoutError, EndpointConnectionError, ReadTimeoutError,
)
from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.utils import timezone

from apps.global_config.models import EmailSettings, StorageSettings

SMTP_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'


# --------------------------------------------------------------------- R2

@dataclass(frozen=True)
class R2Config:
    account_id: str = ''
    access_key_id: str = ''
    secret_access_key: str = ''
    bucket_name: str = ''
    public_url: str = ''
    source: str = 'none'  # 'saved' (Settings page), 'server' (.env) or 'none'

    @property
    def endpoint_url(self):
        return f'https://{self.account_id}.r2.cloudflarestorage.com'

    @property
    def ready(self):
        return bool(self.account_id and self.access_key_id and self.secret_access_key and self.bucket_name)


def r2_config():
    saved = StorageSettings.load()
    if saved is not None:
        return R2Config(
            account_id=saved.account_id,
            access_key_id=saved.access_key_id,
            secret_access_key=saved.secret_access_key,
            bucket_name=saved.bucket_name,
            public_url=saved.public_url,
            source='saved',
        )
    cfg = R2Config(
        account_id=settings.R2_ACCOUNT_ID,
        access_key_id=settings.R2_ACCESS_KEY_ID,
        secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        bucket_name=settings.R2_BUCKET_NAME,
        public_url=settings.R2_PUBLIC_URL,
    )
    has_any = cfg.account_id or cfg.access_key_id or cfg.bucket_name
    return replace(cfg, source='server' if has_any else 'none')


def make_r2_client(cfg):
    return boto3.session.Session().client(
        's3',
        endpoint_url=cfg.endpoint_url,
        aws_access_key_id=cfg.access_key_id,
        aws_secret_access_key=cfg.secret_access_key,
        region_name='auto',
        config=Config(connect_timeout=10, read_timeout=30, retries={'max_attempts': 3}),
    )


R2_ERRORS = {
    'InvalidAccessKeyId': 'The access key ID is wrong.',
    'SignatureDoesNotMatch': 'The secret access key is wrong.',
    'NoSuchBucket': 'There is no bucket with this name in this account.',
    'AccessDenied': "This key isn't allowed to use this bucket.",
    'Unauthorized': "This key isn't allowed to use this bucket.",
    'InvalidBucketName': 'The bucket name is not valid.',
}


def r2_error_message(exc):
    if isinstance(exc, ClientError):
        code = exc.response.get('Error', {}).get('Code', '')
        return R2_ERRORS.get(code) or f'Cloudflare answered: {code or exc}'
    if isinstance(exc, EndpointConnectionError):
        return "Couldn't reach Cloudflare. Check the account ID."
    if isinstance(exc, (ConnectTimeoutError, ReadTimeoutError)):
        return 'Cloudflare took too long to answer.'
    return str(exc) or exc.__class__.__name__


TEST_FILE_BYTES = 1024


def run_r2_test(cfg):
    """Upload a 1 KB file, read it back, delete it, and check it's gone.

    Returns {'ok', 'key', 'steps': [{'name', 'ok', 'ms', 'error'}]}. Stops at
    the first failure; if the upload worked, the file is still deleted.
    """
    key = f'crm-connection-test/{uuid.uuid4().hex}.txt'
    line = b'SkillShikshya CRM connection test\n'
    body = (line * (TEST_FILE_BYTES // len(line) + 1))[:TEST_FILE_BYTES]
    client = make_r2_client(cfg)
    steps = []

    def step(name, fn):
        started = time.monotonic()
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - every failure is shown to the admin
            steps.append({'name': name, 'ok': False, 'ms': _ms(started), 'error': r2_error_message(exc)})
            return False
        steps.append({'name': name, 'ok': True, 'ms': _ms(started), 'error': None})
        return True

    def upload():
        client.put_object(Bucket=cfg.bucket_name, Key=key, Body=body, ContentType='text/plain')

    def read_back():
        got = client.get_object(Bucket=cfg.bucket_name, Key=key)['Body'].read()
        if got != body:
            raise ValueError(f'Read back {len(got)} bytes instead of {len(body)}.')

    def delete():
        client.delete_object(Bucket=cfg.bucket_name, Key=key)

    def check_gone():
        try:
            client.head_object(Bucket=cfg.bucket_name, Key=key)
        except ClientError as exc:
            if exc.response.get('Error', {}).get('Code') in ('404', 'NoSuchKey', 'NotFound'):
                return
            raise
        raise ValueError('The file is still there after deleting it.')

    if step('Upload a 1 KB file', upload):
        read_ok = step('Read it back', read_back)
        if step('Delete it', delete) and read_ok:
            step("Check it's gone", check_gone)

    ok = all(s['ok'] for s in steps) and len(steps) == 4
    return {'ok': ok, 'key': key, 'steps': steps}


def _ms(started):
    return round((time.monotonic() - started) * 1000)


# ------------------------------------------------------------------ Email

@dataclass(frozen=True)
class EmailConfig:
    host: str = ''
    port: int = 587
    security: str = EmailSettings.Security.STARTTLS
    username: str = ''
    password: str = ''
    from_email: str = ''
    source: str = 'none'

    @property
    def ready(self):
        return bool(self.host and self.port)

    def connection(self, timeout=20):
        return get_connection(
            SMTP_BACKEND,
            host=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            use_tls=self.security == EmailSettings.Security.STARTTLS,
            use_ssl=self.security == EmailSettings.Security.SSL,
            timeout=timeout,
            fail_silently=False,
        )


def email_config():
    saved = EmailSettings.load()
    if saved is not None:
        return EmailConfig(
            host=saved.host,
            port=saved.port,
            security=saved.security,
            username=saved.username,
            password=saved.password,
            from_email=saved.from_email,
            source='saved',
        )
    if getattr(settings, 'EMAIL_USE_SSL', False):
        security = EmailSettings.Security.SSL
    elif settings.EMAIL_USE_TLS:
        security = EmailSettings.Security.STARTTLS
    else:
        security = EmailSettings.Security.NONE
    return EmailConfig(
        host=settings.EMAIL_HOST,
        port=settings.EMAIL_PORT,
        security=security,
        username=settings.EMAIL_HOST_USER,
        password=settings.EMAIL_HOST_PASSWORD,
        from_email=settings.DEFAULT_FROM_EMAIL,
        # EMAIL_HOST has a code default (smtp.gmail.com), so a login is what shows it was set up.
        source='server' if settings.EMAIL_HOST and settings.EMAIL_HOST_USER else 'none',
    )


def email_error_message(exc, cfg):
    where = f'{cfg.host}:{cfg.port}'
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return 'The username or password is wrong. For Gmail, use an app password.'
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return 'The server refused the From address.'
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return 'The server refused the To address.'
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return "The server doesn't support this security setting."
    if isinstance(exc, ssl.SSLError):
        return 'The secure connection failed. Check the port and security setting.'
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return 'The server closed the connection. Check the port and security setting.'
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return f'{where} took too long to answer. Check the port and security setting.'
    if isinstance(exc, socket.gaierror):
        return f"Couldn't find the server {cfg.host}."
    if isinstance(exc, smtplib.SMTPConnectError):
        return f"Couldn't connect to {where}."
    if isinstance(exc, smtplib.SMTPException):
        return f'The server answered: {exc}'
    if isinstance(exc, OSError):
        return f"Couldn't connect to {where}."
    return str(exc) or exc.__class__.__name__


def send_test_email(cfg, to, *, sent_by):
    started = time.monotonic()
    when = timezone.localtime().strftime('%d %b %Y, %I:%M %p')
    message = EmailMessage(
        subject='SkillShikshya CRM test email',
        body=(
            'This is a test email from the CRM Settings page.\n\n'
            f'Sent by {sent_by} on {when}, through {cfg.host}:{cfg.port}.\n'
        ),
        from_email=cfg.from_email or settings.DEFAULT_FROM_EMAIL,
        to=[to],
        connection=cfg.connection(timeout=15),
    )
    try:
        message.send()
    except Exception as exc:  # noqa: BLE001 - every failure is shown to the admin
        return {'ok': False, 'ms': _ms(started), 'error': email_error_message(exc, cfg)}
    return {'ok': True, 'ms': _ms(started), 'error': None}


def valid_from_address(value):
    name, address = parseaddr(value or '')
    return bool(address and '@' in address and '.' in address.rsplit('@', 1)[-1])
