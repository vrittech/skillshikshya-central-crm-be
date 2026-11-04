from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.course.models import Course
from apps.lead.models import Lead

User = get_user_model()


class PrimaryCourseNestedTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email='admin@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.course = Course.objects.create(name='Python Advanced', full_amount=30000)
        self.lead = Lead.objects.create(
            full_name='Test Lead', phone='9800000055', primary_course=self.course,
        )

    def test_detail_returns_nested_primary_course(self):
        response = self.client.get(f'/api/leads/{self.lead.id}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        pc = response.data['primary_course']
        self.assertEqual(pc, {'id': self.course.id, 'name': 'Python Advanced', 'price': '30000.00'})
        self.assertNotIn('primary_course_name', response.data)

    def test_null_primary_course(self):
        lead = Lead.objects.create(full_name='No Course', phone='9800000056')
        response = self.client.get(f'/api/leads/{lead.id}/')
        self.assertIsNone(response.data['primary_course'])

    def test_patch_accepts_nested_object(self):
        other = Course.objects.create(name='Flutter', full_amount=25000)
        response = self.client.patch(
            f'/api/leads/{self.lead.id}/',
            {'primary_course': {'id': other.id}}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['primary_course']['id'], other.id)

    def test_patch_still_accepts_bare_int(self):
        bare = Course.objects.create(name='DevOps', full_amount=None)
        response = self.client.patch(
            f'/api/leads/{self.lead.id}/',
            {'primary_course': bare.id}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['primary_course'], {
            'id': bare.id, 'name': 'DevOps', 'price': None,
        })

    def test_patch_invalid_course_rejected(self):
        response = self.client.patch(
            f'/api/leads/{self.lead.id}/',
            {'primary_course': 99999}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

class LeadSourceDeletionTests(APITestCase):
    def setUp(self):
        from apps.lead.models import LeadSource
        self.admin = User.objects.create_user(
            email='admin@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.protected = LeadSource.objects.create(
            name='Protected Source', is_protected=True,
        )
        self.used = LeadSource.objects.create(name='Used Source')
        Lead.objects.create(full_name='Someone', phone='1', source=self.used)
        self.free = LeadSource.objects.create(name='Free Source')

    def test_protected_source_cannot_be_deleted(self):
        response = self.client.delete(f'/api/lead-sources/{self.protected.id}/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('protected', response.data['detail'])
        self.assertTrue(
            __import__('apps.lead.models', fromlist=['LeadSource'])
            .LeadSource.objects.filter(pk=self.protected.id).exists()
        )

    def test_used_source_cannot_be_deleted(self):
        response = self.client.delete(f'/api/lead-sources/{self.used.id}/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('used in leads', response.data['detail'])

    def test_unused_unprotected_source_deletes(self):
        response = self.client.delete(f'/api/lead-sources/{self.free.id}/')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_is_protected_toggle_via_patch(self):
        response = self.client.patch(
            f'/api/lead-sources/{self.free.id}/',
            {'is_protected': True}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['is_protected'])
        # now deletion must be blocked
        blocked = self.client.delete(f'/api/lead-sources/{self.free.id}/')
        self.assertEqual(blocked.status_code, status.HTTP_400_BAD_REQUEST)

    def test_model_level_delete_raises_protected_error(self):
        from django.db import transaction
        from django.db.models import ProtectedError
        with self.assertRaises(ProtectedError):
            with transaction.atomic():
                self.protected.delete()
        with self.assertRaises(ProtectedError):
            with transaction.atomic():
                self.used.delete()


class GlobalCallListTests(APITestCase):
    def setUp(self):
        from datetime import timedelta
        from apps.branch.models import Branch
        from django.utils import timezone as tz
        from apps.lead.models import LeadCall

        self.admin = User.objects.create_user(
            email='admin@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.counselor = User.objects.create_user(
            email='c9@test.com', name='Counselor Nine', password='pass',
            is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.branch = Branch.objects.create(name='Biratnagar', code='BRT')
        self.lead = Lead.objects.create(full_name='Call Person', phone='9811111111')
        from apps.student.models import Student
        self.student = Student.objects.create(
            full_name='Call Person', phone='9811111111', lead=self.lead,
        )
        now = tz.now()
        self.recent_call = LeadCall.objects.create(
            lead=self.lead, call_timestamp=now, direction='outbound',
            caller='Counselor Nine', phone_number='9811111111',
            duration_seconds=270, outcome='interested',
            created_by=self.counselor, branch=self.branch,
        )
        self.old_call = LeadCall.objects.create(
            lead=self.lead, call_timestamp=now - timedelta(days=10),
            direction='inbound', caller='Someone Else',
            phone_number='9822222222', duration_seconds=60,
            created_by=self.admin, branch=self.branch,
        )

    def test_list_returns_all_calls_newest_first(self):
        response = self.client.get('/api/calls/')
        self.assertEqual(response.status_code, 200)
        ids = [r['id'] for r in response.data['results']]
        self.assertEqual(ids, [self.recent_call.id, self.old_call.id])
        row = response.data['results'][0]
        self.assertEqual(row['lead_name'], 'Call Person')
        self.assertTrue(row['lead_reference_number'].startswith('LD-'))

    def test_filter_direction(self):
        response = self.client.get('/api/calls/?direction=inbound')
        self.assertEqual([r['id'] for r in response.data['results']], [self.old_call.id])

    def test_filter_created_by_user(self):
        response = self.client.get(f'/api/calls/?created_by={self.counselor.id}')
        self.assertEqual(
            [r['id'] for r in response.data['results']], [self.recent_call.id],
        )

    def test_filter_by_student(self):
        response = self.client.get(f'/api/calls/?student={self.student.id}')
        self.assertEqual(response.data['count'], 2)

    def test_filter_date_range(self):
        from django.utils import timezone as tz
        today = tz.now().date().isoformat()
        response = self.client.get(f'/api/calls/?call_timestamp__gte={today}')
        self.assertEqual(
            [r['id'] for r in response.data['results']], [self.recent_call.id],
        )

    def _call_at(self, year, month, day, hour, minute):
        from datetime import datetime
        from apps.lead.filters import NEPAL_TZ
        from apps.lead.models import LeadCall
        when = datetime(year, month, day, hour, minute, tzinfo=NEPAL_TZ)
        return LeadCall.objects.create(
            lead=self.lead, call_timestamp=when, direction='outbound',
            caller='Counselor Nine', phone_number='9811111111', duration_seconds=30,
            created_by=self.counselor, branch=self.branch,
        )

    def _call_in_other_branch(self):
        from apps.branch.models import Branch
        other_branch = Branch.objects.create(name='Dharan', code='DHR')
        call = self._call_at(2020, 1, 15, 10, 0)
        call.branch = other_branch
        call.notes = 'Dharan only'
        call.save(update_fields=['branch', 'notes'])
        return other_branch, call

    def test_date_to_includes_end_of_day_npt(self):
        late_call = self._call_at(2020, 1, 15, 23, 59)
        response = self.client.get('/api/calls/?date_from=2020-01-15&date_to=2020-01-15')
        self.assertEqual([r['id'] for r in response.data['results']], [late_call.id])

    def test_date_filters_use_npt_days(self):
        # 00:10 NPT on Jan 16 is still Jan 15 in UTC
        early_call = self._call_at(2020, 1, 16, 0, 10)
        response = self.client.get('/api/calls/?date_from=2020-01-15&date_to=2020-01-15')
        self.assertEqual(response.data['count'], 0)
        jan_16 = self.client.get('/api/calls/?date_from=2020-01-16&date_to=2020-01-16')
        self.assertEqual([r['id'] for r in jan_16.data['results']], [early_call.id])

    def test_summary(self):
        response = self.client.get('/api/calls/summary/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total_calls'], 2)
        self.assertEqual(response.data['inbound'], 1)
        self.assertEqual(response.data['outbound'], 1)
        self.assertEqual(response.data['total_seconds'], 330)
        self.assertEqual(response.data['average_seconds'], 165)
        self.assertEqual(response.data['connected'], 2)
        self.assertEqual(response.data['connect_rate'], 100.0)
        self.assertEqual(
            [(c['user_id'], c['count']) for c in response.data['top_callers']],
            [(self.admin.id, 1), (self.counselor.id, 1)],
        )

    def test_summary_empty(self):
        response = self.client.get('/api/calls/summary/?date_from=1999-01-01&date_to=1999-01-01')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total_calls'], 0)
        self.assertEqual(response.data['average_seconds'], 0)
        self.assertEqual(response.data['connect_rate'], 0)
        self.assertEqual(response.data['calls_per_day'], [])
        self.assertEqual(response.data['top_callers'], [])

    def test_summary_npt_days(self):
        # 00:30 NPT on Jan 16 is still Jan 15 in UTC
        self._call_at(2020, 1, 15, 23, 30)
        self._call_at(2020, 1, 16, 0, 30)
        response = self.client.get('/api/calls/summary/?date_from=2020-01-15&date_to=2020-01-16')
        self.assertEqual(response.data['calls_per_day'], [
            {'date': '2020-01-15', 'count': 1},
            {'date': '2020-01-16', 'count': 1},
        ])

    def test_summary_matches_list_count(self):
        self._call_at(2020, 1, 15, 10, 0)
        for params in ('', '?direction=outbound', '?search=Counselor Nine', '?date_to=2020-01-15'):
            list_count = self.client.get(f'/api/calls/{params}').data['count']
            summary = self.client.get(f'/api/calls/summary/{params}').data
            self.assertEqual(summary['total_calls'], list_count, msg=params)

    def test_summary_branch_scoping_for_non_staff(self):
        self._call_in_other_branch()
        branch_user = User.objects.create_user(
            email='brt@test.com', name='BRT User', password='x', branch=self.branch,
        )
        self.client.force_authenticate(branch_user)
        response = self.client.get('/api/calls/summary/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total_calls'], 2)
        self.assertEqual(response.data['total_seconds'], 330)
        self.assertNotIn('2020-01-15', [d['date'] for d in response.data['calls_per_day']])

    def test_summary_query_count(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as before:
            self.client.get('/api/calls/summary/')
        for minute in range(20):
            self._call_at(2020, 1, 15, 10, minute)
        with self.assertNumQueries(len(before)):
            self.client.get('/api/calls/summary/')

    def test_search(self):
        response = self.client.get('/api/calls/?search=Counselor Nine')
        self.assertEqual(response.data['count'], 1)
        search_lead = self.client.get('/api/calls/?search=Call Person')
        self.assertEqual(search_lead.data['count'], 2)

    def test_branch_scoping_for_non_staff(self):
        from apps.branch.models import Branch
        from apps.lead.models import LeadCall
        other_branch = Branch.objects.create(name='Dharan', code='DHR')
        other_admin = User.objects.create_user(
            email='br@test.com', name='BR User', password='x',
            branch=other_branch,
        )
        LeadCall.objects.filter(id=self.recent_call.id).update(branch=None)
        self.client.force_authenticate(other_admin)
        response = self.client.get('/api/calls/')
        self.assertNotIn(self.recent_call.id, [r['id'] for r in response.data['results']])
