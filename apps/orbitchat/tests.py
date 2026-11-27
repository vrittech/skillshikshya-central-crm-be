from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework.test import APITestCase
from django.utils import timezone

from apps.course.models import Course
from apps.lead.intake import lead_intake  # noqa: F401  (import sanity)
from apps.lead.models import Lead, LeadCourseInterest
from apps.orbitchat.models import SyncState
from apps.orbitchat.services import create_lead_from_orbitchat_contact
from apps.orbitchat.tasks import poll_orbitchat_leads_task


class OrbitchatPrimaryCourseTests(TestCase):
    def setUp(self):
        self.course = Course.objects.create(
            name='Data Science and Machine Learning Diploma Course',
        )

    def test_contact_sets_interest_and_primary_course(self):
        contact = {
            'id': '52be7ae7-fcf7-4578-b61c-6079b0e155a1',
            'name': 'Rajan Khatri',
            'phone': None,
            'email': '',
            'channel_type': 'facebook',
            'notes': 'Data Science and Machine Learning Diploma Course',
        }
        lead = create_lead_from_orbitchat_contact(contact)

        self.assertIsNotNone(lead)
        self.assertEqual(lead.primary_course_id, self.course.id)

        interest = LeadCourseInterest.objects.get(lead=lead)
        self.assertTrue(interest.is_primary)
        self.assertEqual(interest.course_id, self.course.id)


class PublicIntakePrimaryCourseTests(TestCase):
    def setUp(self):
        self.python = Course.objects.create(name='Python Advanced')
        Course.objects.create(name='Flutter')

    @override_settings(LEAD_INTAKE_TOKEN='t')
    def test_first_course_becomes_primary_course(self):
        from rest_framework.test import APIClient

        response = APIClient(HTTP_X_INTAKE_TOKEN='t').post('/api/public/lead-intake/', {
            'name': 'Intake Person',
            'phone_number': '9812340000',
            'course_interests': ['Python Advanced', 'Flutter'],
        }, format='json')
        self.assertEqual(response.status_code, 201)

        lead = Lead.objects.get(id=response.data['id'])
        self.assertEqual(lead.primary_course_id, self.python.id)
        self.assertEqual(lead.course_interests.count(), 2)


class BackfillCommandTests(TestCase):
    def test_backfill_sets_missing_primary_courses(self):
        course = Course.objects.create(name='DevOps Course in Nepal')
        lead = Lead.objects.create(full_name='Old Lead', phone='111')
        LeadCourseInterest.objects.create(
            lead=lead, course=course, is_primary=True,
        )
        self.assertIsNone(lead.primary_course_id)

        out = StringIO()
        call_command('backfill_primary_courses', stdout=out)

        lead.refresh_from_db()
        self.assertEqual(lead.primary_course_id, course.id)
        self.assertIn('Updated 1 leads', out.getvalue())

    def test_backfill_skips_leads_with_existing_primary(self):
        keep = Course.objects.create(name='Keep Me')
        other = Course.objects.create(name='Other')
        lead = Lead.objects.create(full_name='Already Set', phone='222')
        lead.primary_course = keep
        lead.save(update_fields=['primary_course'])
        LeadCourseInterest.objects.create(lead=lead, course=other, is_primary=True)

        out = StringIO()
        call_command('backfill_primary_courses', stdout=out)

        lead.refresh_from_db()
        self.assertEqual(lead.primary_course_id, keep.id)

class WatermarkSyncTests(TestCase):
    def _run_task(self, contacts, **kwargs):
        kwargs.setdefault('force', True)  # bypass toggle; gating tested separately
        with mock.patch(
            'apps.orbitchat.client.OrbitChatClient.login', return_value='tok',
        ), mock.patch(
            'apps.orbitchat.client.OrbitChatClient.fetch_contacts',
            return_value=contacts,
        ) as mock_fetch:
            result = poll_orbitchat_leads_task(**kwargs)
        return result, mock_fetch

    def test_first_run_uses_default_window_and_sets_watermark(self):
        state = SyncState.get()
        self.assertIsNone(state.last_sync_at)

        result, fetch = self._run_task([])
        kwargs = fetch.call_args.kwargs
        self.assertEqual(
            (kwargs['end_date'] - kwargs['start_date']).days, 1,
        )
        SyncState.get().refresh_from_db()
        self.assertIsNotNone(SyncState.get().last_sync_at)
        self.assertTrue(result['watermark_updated'])

    def test_steady_state_fetches_today_only(self):
        SyncState.get()  # create row
        yesterday = timezone.now() - timedelta(hours=2)
        SyncState.objects.update(last_sync_at=yesterday)

        _, fetch = self._run_task([])
        kwargs = fetch.call_args.kwargs
        today = timezone.now().date()
        self.assertEqual(kwargs['start_date'], today)
        self.assertEqual(kwargs['end_date'], today)

    def test_manual_window_does_not_move_watermark(self):
        before = timezone.now() - timedelta(days=5)
        state = SyncState.get()  # ensure the row exists
        state.last_sync_at = before
        state.save(update_fields=['last_sync_at'])

        result, _ = self._run_task(
            [], start_date='2026-08-01', end_date='2026-08-02',
        )
        SyncState.get().refresh_from_db()
        self.assertEqual(SyncState.get().last_sync_at, before)
        self.assertFalse(result['watermark_updated'])

    def test_failed_run_keeps_watermark(self):
        SyncState.get()
        before = timezone.now() - timedelta(hours=3)
        SyncState.objects.update(last_sync_at=before)

        with mock.patch(
            'apps.orbitchat.client.OrbitChatClient.login',
            side_effect=Exception('api down'),
        ):
            with self.assertRaises(Exception):
                poll_orbitchat_leads_task(force=True)

        SyncState.get().refresh_from_db()
        self.assertEqual(SyncState.get().last_sync_at, before)

    def test_dedup_across_repeated_runs(self):
        contact = {
            'id': 'dup-1', 'name': 'Repeat Person', 'phone': None,
            'channel_type': 'facebook', 'notes': '',
        }
        self._run_task([contact])
        created_first = Lead.objects.count()

        # second run re-fetches the same window; dedup must skip
        result, _ = self._run_task([contact])
        self.assertEqual(Lead.objects.count(), created_first)
        self.assertEqual(result['skipped'], 1)

class SyncToggleTests(TestCase):
    def _run_task(self, contacts, **kwargs):
        with mock.patch(
            'apps.orbitchat.client.OrbitChatClient.login', return_value='tok',
        ), mock.patch(
            'apps.orbitchat.client.OrbitChatClient.fetch_contacts',
            return_value=contacts,
        ):
            return poll_orbitchat_leads_task(**kwargs)

    def test_disabled_auto_run_is_noop(self):
        state = SyncState.get()  # is_enabled defaults False
        before = timezone.now()
        state.last_sync_at = before
        state.save(update_fields=['last_sync_at'])

        result = self._run_task([])
        self.assertEqual(result['status'], 'disabled')
        state.refresh_from_db()
        self.assertEqual(state.last_sync_at, before)

    def test_enabled_runs(self):
        state = SyncState.get()
        state.is_enabled = True
        state.save(update_fields=['is_enabled'])

        result = self._run_task([])
        self.assertNotEqual(result.get('status'), 'disabled')
        self.assertTrue(result['watermark_updated'])

    def test_force_bypasses_disabled(self):
        SyncState.get()  # disabled

        result = self._run_task([], force=True)
        self.assertNotEqual(result.get('status'), 'disabled')

    def test_manual_dates_respect_disabled_flag(self):
        # manual backfills without force are also gated by the flag
        result = self._run_task(
            [], start_date='2026-08-01', end_date='2026-08-02',
        )
        self.assertEqual(result['status'], 'disabled')

        forced = self._run_task(
            [], start_date='2026-08-01', end_date='2026-08-02', force=True,
        )
        self.assertNotEqual(forced.get('status'), 'disabled')


class SyncStateAPITests(APITestCase):
    def _client(self, staff):
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIClient
        User = get_user_model()
        user = User.objects.create_user(
            email=f'{"admin" if staff else "c"}@t.com', name='U',
            password='x', is_staff=staff,
        )
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_staff_can_get_state(self):
        client = self._client(staff=True)
        response = client.get('/api/orbitchat/sync-state/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['is_enabled'])
        self.assertEqual(response.data['key'], 'orbitchat_leads')

    def test_staff_can_toggle_enabled(self):
        from apps.orbitchat.models import SyncState
        client = self._client(staff=True)
        response = client.patch(
            '/api/orbitchat/sync-state/', {'is_enabled': True}, format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['is_enabled'])
        self.assertTrue(SyncState.get().is_enabled)

    def test_non_staff_denied(self):
        client = self._client(staff=False)
        get = client.get('/api/orbitchat/sync-state/')
        patch = client.patch(
            '/api/orbitchat/sync-state/', {'is_enabled': True}, format='json',
        )
        self.assertIn(get.status_code, (403, 401))
        self.assertIn(patch.status_code, (403, 401))

    def test_key_and_last_sync_read_only(self):
        client = self._client(staff=True)
        response = client.patch('/api/orbitchat/sync-state/', {
            'key': 'hacked',
            'last_sync_at': '2020-01-01T00:00:00Z',
            'is_enabled': True,
        }, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['key'], 'orbitchat_leads')
        self.assertIsNone(response.data['last_sync_at'])

