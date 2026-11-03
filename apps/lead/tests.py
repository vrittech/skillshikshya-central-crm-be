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


from datetime import datetime as _dt, timezone as _utc
import csv as _csv
import io as _io
from django.db import connection as _connection
from django.test.utils import CaptureQueriesContext as _CaptureQueries


class CallSummaryExportTests(APITestCase):
    def setUp(self):
        from apps.branch.models import Branch
        from apps.lead.models import LeadCall

        self.brt = Branch.objects.create(name='Biratnagar', code='BRT')
        self.dhr = Branch.objects.create(name='Dharan', code='DHR')
        self.admin = User.objects.create_user(
            email='admin2@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.brt_user = User.objects.create_user(
            email='brt@test.com', name='Sita', password='pass',
        )
        self.brt_user.branch = self.brt
        self.brt_user.save()
        self.lead = Lead.objects.create(full_name='Call Person', phone='9811111111')

        def make_call(branch, ts, seconds, direction='outbound', caller='Sita', notes=None):
            return LeadCall.objects.create(
                lead=self.lead, call_timestamp=ts, direction=direction, caller=caller,
                phone_number='9811111111', duration_seconds=seconds, notes=notes,
                created_by=self.brt_user, branch=branch,
            )
        self.make_call = make_call

        # Nepal is UTC+5:45. 17:00 UTC = 22:45 NPT on 1 Oct; 18:20 UTC = 00:05 NPT on 2 Oct.
        make_call(self.brt, _dt(2026, 10, 1, 17, 0, tzinfo=_utc.utc), 120)
        make_call(self.brt, _dt(2026, 10, 1, 18, 20, tzinfo=_utc.utc), 0, direction='inbound')
        make_call(self.brt, _dt(2026, 10, 2, 6, 0, tzinfo=_utc.utc), 60, notes='=HYPERLINK("x")')
        make_call(self.dhr, _dt(2026, 10, 2, 7, 0, tzinfo=_utc.utc), 500, caller='Ram')

    def _export_body(self, url='/api/calls/export/'):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return b''.join(response.streaming_content).decode('utf-8')

    def test_non_staff_sees_only_own_branch_everywhere(self):
        self.client.force_authenticate(self.brt_user)
        listing = self.client.get('/api/calls/')
        self.assertEqual(len(listing.data['results']), 3)
        summary = self.client.get('/api/calls/summary/')
        self.assertEqual(summary.data['total_calls'], 3)
        body = self._export_body()
        self.assertNotIn('Ram', body)
        self.assertEqual(len(list(_csv.reader(_io.StringIO(body.lstrip('\ufeff'))))), 4)

    def test_non_staff_cannot_widen_scope_with_branch_id(self):
        self.client.force_authenticate(self.brt_user)
        response = self.client.get(f'/api/calls/summary/?branch_id={self.dhr.id}')
        self.assertEqual(response.data['total_calls'], 3)

    def test_staff_sees_all_branches_and_can_narrow(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get('/api/calls/summary/').data['total_calls'], 4)
        narrowed = self.client.get(f'/api/calls/summary/?branch_id={self.dhr.id}')
        self.assertEqual(narrowed.data['total_calls'], 1)

    def test_summary_numbers(self):
        self.client.force_authenticate(self.admin)
        data = self.client.get(f'/api/calls/summary/?branch_id={self.brt.id}').data
        self.assertEqual(data['total_calls'], 3)
        self.assertEqual(data['inbound'], 1)
        self.assertEqual(data['outbound'], 2)
        self.assertEqual(data['talk_seconds'], 180)
        self.assertEqual(data['average_seconds'], 60)
        self.assertEqual(data['connected_calls'], 2)
        self.assertEqual(data['connect_rate'], 66.7)
        self.assertEqual(data['top_callers'][0], {'caller': 'Sita', 'calls': 3, 'talk_seconds': 180})

    def test_summary_with_no_matching_calls_is_all_zero(self):
        self.client.force_authenticate(self.admin)
        data = self.client.get('/api/calls/summary/?outcome=nothing-matches').data
        self.assertEqual(data['total_calls'], 0)
        self.assertEqual(data['connect_rate'], 0)
        self.assertEqual(data['calls_per_day'], [])
        self.assertEqual(data['top_callers'], [])

    def test_days_are_counted_in_nepal_time(self):
        self.client.force_authenticate(self.admin)
        data = self.client.get(f'/api/calls/summary/?branch_id={self.brt.id}').data
        per_day = {row['date']: row['count'] for row in data['calls_per_day']}
        self.assertEqual(per_day, {'2026-10-01': 1, '2026-10-02': 2})

    def test_export_has_bom_header_and_blocks_formulas(self):
        self.client.force_authenticate(self.admin)
        body = self._export_body()
        self.assertTrue(body.startswith('\ufeff'))
        rows = list(_csv.reader(_io.StringIO(body.lstrip('\ufeff'))))
        self.assertEqual(rows[0][0], 'Date & time (NPT)')
        notes_col = rows[0].index('Notes')
        self.assertIn('\'=HYPERLINK("x")', [r[notes_col] for r in rows[1:]])
        self.assertEqual(rows[1][0][:10] in ('2026-10-02',), True)  # newest first, NPT date

    def test_query_count_does_not_grow_with_rows(self):
        self.client.force_authenticate(self.admin)
        urls = ('/api/calls/', '/api/calls/summary/', '/api/calls/export/')

        def count(url):
            with _CaptureQueries(_connection) as ctx:
                response = self.client.get(url)
                if getattr(response, 'streaming', False):
                    b''.join(response.streaming_content)
            return len(ctx)

        before = {u: count(u) for u in urls}
        for i in range(15):
            self.make_call(self.brt, _dt(2026, 10, 3, 5, i, tzinfo=_utc.utc), 30)
        after = {u: count(u) for u in urls}
        self.assertEqual(before, after)
