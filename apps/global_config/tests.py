import smtplib
from unittest import mock

from botocore.exceptions import ClientError
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.mail import EmailMessage, get_connection
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.global_config import integrations
from apps.global_config.mail import SavedSettingsEmailBackend
from apps.global_config.models import EmailSettings, StorageSettings

User = get_user_model()

ACCOUNT = 'a' * 32
NO_R2 = dict(R2_ACCOUNT_ID='', R2_ACCESS_KEY_ID='', R2_SECRET_ACCESS_KEY='', R2_BUCKET_NAME='', R2_PUBLIC_URL='')
ENV_R2 = dict(
    R2_ACCOUNT_ID='b' * 32, R2_ACCESS_KEY_ID='env-key', R2_SECRET_ACCESS_KEY='env-secret-value',
    R2_BUCKET_NAME='env-bucket', R2_PUBLIC_URL='',
)
ENV_SMTP = dict(
    EMAIL_HOST='smtp.env.test', EMAIL_PORT=587, EMAIL_USE_TLS=True, EMAIL_USE_SSL=False,
    EMAIL_HOST_USER='env@env.test', EMAIL_HOST_PASSWORD='env-pass',
    DEFAULT_FROM_EMAIL='Skill Shikshya <noreply@env.test>',
)


def client_error(code):
    return ClientError({'Error': {'Code': code, 'Message': code}}, 'Op')


class FakeR2:
    """Stands in for the boto3 client: keeps objects in a dict."""

    def __init__(self, fail_on=None, error='InvalidAccessKeyId'):
        self.objects = {}
        self.fail_on = fail_on
        self.error = error
        self.calls = []

    def _maybe_fail(self, name):
        self.calls.append(name)
        if name == self.fail_on:
            raise client_error(self.error)

    def put_object(self, Bucket, Key, Body, ContentType):
        self._maybe_fail('put_object')
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):
        self._maybe_fail('get_object')
        return {'Body': mock.Mock(read=lambda: self.objects[Key])}

    def delete_object(self, Bucket, Key):
        self._maybe_fail('delete_object')
        self.objects.pop(Key, None)

    def head_object(self, Bucket, Key):
        self._maybe_fail('head_object')
        if Key not in self.objects:
            raise client_error('404')
        return {'ContentLength': len(self.objects[Key])}


class SettingsAccessTests(APITestCase):
    def test_only_admins(self):
        user = User.objects.create_user(email='c@test.com', name='Counselor', password='pass')
        self.client.force_authenticate(user)
        for url in ('/api/settings/storage/', '/api/settings/email/'):
            self.assertEqual(self.client.get(url).status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            self.client.post('/api/settings/email/test/', {'to': 'a@b.com'}).status_code,
            status.HTTP_403_FORBIDDEN,
        )


@override_settings(**NO_R2)
class StorageSettingsTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(email='admin@test.com', name='Admin', password='pass', is_staff=True)
        self.client.force_authenticate(self.admin)

    def form(self, **overrides):
        data = {
            'account_id': ACCOUNT, 'access_key_id': 'key-1', 'secret_access_key': 'secret-one-1234',
            'bucket_name': 'crm-files', 'public_url': '',
        }
        data.update(overrides)
        return data

    def test_nothing_set(self):
        res = self.client.get('/api/settings/storage/')
        self.assertEqual(res.data['source'], 'none')
        self.assertFalse(res.data['ready'])

    @override_settings(**ENV_R2)
    def test_server_values_show_without_the_secret(self):
        res = self.client.get('/api/settings/storage/')
        self.assertEqual(res.data['source'], 'server')
        self.assertEqual(res.data['bucket_name'], 'env-bucket')
        self.assertTrue(res.data['secret_access_key_set'])
        self.assertNotIn('env-secret-value', str(res.data))

    def test_save_encrypts_the_secret_and_hides_it(self):
        res = self.client.put('/api/settings/storage/', self.form(), format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertEqual(res.data['source'], 'saved')
        self.assertTrue(res.data['ready'])
        self.assertEqual(res.data['updated_by_name'], 'Admin')
        self.assertNotIn('secret-one-1234', str(res.data))
        row = StorageSettings.objects.get()
        self.assertNotIn('secret-one-1234', row.secret_access_key_encrypted)
        self.assertEqual(row.secret_access_key, 'secret-one-1234')

    def test_blank_secret_keeps_the_saved_one(self):
        self.client.put('/api/settings/storage/', self.form(), format='json')
        res = self.client.put(
            '/api/settings/storage/', self.form(secret_access_key='', bucket_name='other-bucket'), format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        row = StorageSettings.objects.get()
        self.assertEqual(row.bucket_name, 'other-bucket')
        self.assertEqual(row.secret_access_key, 'secret-one-1234')

    @override_settings(**ENV_R2)
    def test_first_save_can_keep_the_server_secret(self):
        res = self.client.put('/api/settings/storage/', self.form(secret_access_key=''), format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertEqual(StorageSettings.objects.get().secret_access_key, 'env-secret-value')

    def test_secret_needed_when_none_in_use(self):
        res = self.client.put('/api/settings/storage/', self.form(secret_access_key=''), format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('secret_access_key', res.data)

    def test_bad_account_id_and_bucket(self):
        res = self.client.put(
            '/api/settings/storage/', self.form(account_id='abc', bucket_name='Bad_Bucket'), format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('account_id', res.data)
        self.assertIn('bucket_name', res.data)

    @override_settings(**ENV_R2)
    def test_saved_values_win_over_server_values(self):
        self.client.put('/api/settings/storage/', self.form(), format='json')
        cfg = integrations.r2_config()
        self.assertEqual((cfg.source, cfg.bucket_name, cfg.access_key_id), ('saved', 'crm-files', 'key-1'))
        self.assertEqual(cfg.endpoint_url, f'https://{ACCOUNT}.r2.cloudflarestorage.com')

    def test_connection_test_uploads_reads_and_deletes(self):
        fake = FakeR2()
        with mock.patch.object(integrations, 'make_r2_client', return_value=fake) as make:
            res = self.client.post('/api/settings/storage/test/', self.form(), format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data['ok'])
        self.assertEqual([s['name'] for s in res.data['steps']],
                         ['Upload a 1 KB file', 'Read it back', 'Delete it', "Check it's gone"])
        self.assertEqual(fake.objects, {})
        self.assertTrue(res.data['key'].startswith('crm-connection-test/'))
        self.assertEqual(make.call_args.args[0].secret_access_key, 'secret-one-1234')
        self.assertFalse(StorageSettings.objects.exists())  # testing doesn't save

    def test_connection_test_reports_a_wrong_key(self):
        fake = FakeR2(fail_on='put_object', error='InvalidAccessKeyId')
        with mock.patch.object(integrations, 'make_r2_client', return_value=fake):
            res = self.client.post('/api/settings/storage/test/', self.form(), format='json')
        self.assertFalse(res.data['ok'])
        self.assertEqual(len(res.data['steps']), 1)
        self.assertEqual(res.data['steps'][0]['error'], 'The access key ID is wrong.')
        self.assertNotIn('delete_object', fake.calls)

    def test_connection_test_still_deletes_when_read_fails(self):
        fake = FakeR2(fail_on='get_object', error='AccessDenied')
        with mock.patch.object(integrations, 'make_r2_client', return_value=fake):
            res = self.client.post('/api/settings/storage/test/', self.form(), format='json')
        self.assertFalse(res.data['ok'])
        self.assertIn('delete_object', fake.calls)
        self.assertEqual(fake.objects, {})

    def test_connection_test_uses_saved_secret_when_blank(self):
        self.client.put('/api/settings/storage/', self.form(), format='json')
        with mock.patch.object(integrations, 'make_r2_client', return_value=FakeR2()) as make:
            self.client.post('/api/settings/storage/test/', self.form(secret_access_key=''), format='json')
        self.assertEqual(make.call_args.args[0].secret_access_key, 'secret-one-1234')

    def test_connection_test_needs_all_values(self):
        res = self.client.post('/api/settings/storage/test/', {}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)


@override_settings(**ENV_SMTP)
class EmailSettingsTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(email='admin@test.com', name='Admin', password='pass', is_staff=True)
        self.client.force_authenticate(self.admin)

    def form(self, **overrides):
        data = {
            'host': 'smtp.saved.test', 'port': 465, 'security': 'ssl', 'username': 'crm@saved.test',
            'password': 'saved-pass', 'from_email': 'CRM <crm@saved.test>',
        }
        data.update(overrides)
        return data

    def test_server_values_show_without_the_password(self):
        res = self.client.get('/api/settings/email/')
        self.assertEqual(res.data['source'], 'server')
        self.assertEqual(res.data['host'], 'smtp.env.test')
        self.assertEqual(res.data['security'], 'starttls')
        self.assertTrue(res.data['password_set'])
        self.assertEqual(res.data['test_to'], 'admin@test.com')
        self.assertNotIn('env-pass', str(res.data))

    @override_settings(EMAIL_HOST='smtp.gmail.com', EMAIL_HOST_USER='', EMAIL_HOST_PASSWORD='')
    def test_code_default_host_alone_is_not_set_up(self):
        res = self.client.get('/api/settings/email/')
        self.assertEqual(res.data['source'], 'none')
        self.assertEqual(res.data['host'], 'smtp.gmail.com')

    def test_save_encrypts_password_and_blank_keeps_it(self):
        res = self.client.put('/api/settings/email/', self.form(), format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        row = EmailSettings.objects.get()
        self.assertNotIn('saved-pass', row.password_encrypted)
        self.assertEqual(row.password, 'saved-pass')
        self.client.put('/api/settings/email/', self.form(password='', port=2525), format='json')
        row.refresh_from_db()
        self.assertEqual((row.port, row.password), (2525, 'saved-pass'))

    def test_no_username_means_no_password(self):
        res = self.client.put('/api/settings/email/', self.form(username='', password='x'), format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertEqual(EmailSettings.objects.get().password, '')

    def test_bad_from_address(self):
        res = self.client.put('/api/settings/email/', self.form(from_email='not an email'), format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('from_email', res.data)

    def test_send_test_email_with_form_values(self):
        locmem = get_connection('django.core.mail.backends.locmem.EmailBackend')
        with mock.patch.object(integrations.EmailConfig, 'connection', return_value=locmem):
            res = self.client.post(
                '/api/settings/email/test/', {**self.form(), 'to': 'me@test.com'}, format='json',
            )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data['ok'], res.data)
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, ['me@test.com'])
        self.assertEqual(sent.from_email, 'CRM <crm@saved.test>')
        self.assertIn('smtp.saved.test:465', sent.body)
        self.assertFalse(EmailSettings.objects.exists())  # testing doesn't save

    def test_send_test_email_reports_a_wrong_password(self):
        failing = mock.Mock()
        failing.send_messages.side_effect = smtplib.SMTPAuthenticationError(535, b'bad credentials')
        with mock.patch.object(integrations.EmailConfig, 'connection', return_value=failing):
            res = self.client.post('/api/settings/email/test/', {'to': 'me@test.com'}, format='json')
        self.assertFalse(res.data['ok'])
        self.assertIn('username or password is wrong', res.data['error'])

    def test_error_messages(self):
        cfg = integrations.EmailConfig(host='smtp.x.test', port=25)
        self.assertIn("Couldn't connect to smtp.x.test:25", integrations.email_error_message(ConnectionRefusedError(), cfg))
        self.assertIn('took too long', integrations.email_error_message(TimeoutError(), cfg))
        self.assertIn('closed the connection', integrations.email_error_message(smtplib.SMTPServerDisconnected(), cfg))


@override_settings(**ENV_SMTP)
class SavedSettingsEmailBackendTests(TestCase):
    def test_uses_server_values_when_nothing_saved(self):
        backend = SavedSettingsEmailBackend()
        self.assertEqual((backend.host, backend.port, backend.username), ('smtp.env.test', 587, 'env@env.test'))
        self.assertTrue(backend.use_tls)

    def test_uses_saved_values(self):
        row = EmailSettings(host='smtp.saved.test', port=465, security='ssl', username='u', from_email='CRM <crm@saved.test>')
        row.password = 'p'
        row.save()
        backend = SavedSettingsEmailBackend()
        self.assertEqual((backend.host, backend.port, backend.username, backend.password),
                         ('smtp.saved.test', 465, 'u', 'p'))
        self.assertTrue(backend.use_ssl)
        self.assertFalse(backend.use_tls)

        default_from = EmailMessage('s', 'b', 'Skill Shikshya <noreply@env.test>', ['x@test.com'])
        own_from = EmailMessage('s', 'b', 'Someone <someone@test.com>', ['x@test.com'])
        backend.use_saved_from([default_from, own_from])
        self.assertEqual(default_from.from_email, 'CRM <crm@saved.test>')
        self.assertEqual(own_from.from_email, 'Someone <someone@test.com>')


class SecretsTests(TestCase):
    def test_round_trip_and_wrong_key(self):
        from apps.global_config.secrets import decrypt, encrypt
        token = encrypt('top-secret')
        self.assertNotIn('top-secret', token)
        self.assertEqual(decrypt(token), 'top-secret')
        with override_settings(SETTINGS_ENCRYPTION_KEY='another-key'):
            self.assertEqual(decrypt(token), '')
        self.assertEqual(encrypt(''), '')
