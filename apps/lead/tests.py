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

class CallsSummaryExportTests(APITestCase):
    """Summary (US-1), export (US-4) and branch scoping (US-6)."""

    def setUp(self):
        import datetime as dt
        from django.utils import timezone as tz
        from apps.branch.models import Branch
        from apps.lead.models import LeadCall

        utc = dt.timezone.utc
        self.admin = User.objects.create_user(
            email='sum-admin@test.com', name='Sum Admin', password='pass', is_staff=True,
        )
        self.brt = Branch.objects.create(name='Biratnagar', code='SBT')
        self.dng = Branch.objects.create(name='Dharan', code='SDH')

        self.brt_user = User.objects.create_user(
            email='brt@test.com', name='Brt User', password='pass',
        )
        self.brt_user.branch = self.brt
        self.brt_user.save()
        self.dng_user = User.objects.create_user(
            email='dng@test.com', name='Dng User', password='pass',
        )
        self.dng_user.branch = self.dng
        self.dng_user.save()

        self.lead = Lead.objects.create(full_name='Sita नेपाली', phone='9800000001')

        def mk(ts, direction, seconds, branch, by, notes=''):
            return LeadCall.objects.create(
                lead=self.lead, call_timestamp=ts, direction=direction,
                caller='x', phone_number='9800000001', duration_seconds=seconds,
                outcome='interested', created_by=by, branch=branch, notes=notes,
            )

        # 20:00 UTC on Oct 28 is 01:45 NPT on Oct 29.
        self.c1 = mk(dt.datetime(2026, 10, 28, 20, 0, tzinfo=utc), 'outbound', 100, self.brt, self.admin)
        # 17:15 UTC is 23:00 NPT, so it stays on Oct 28. Zero-second call.
        self.c2 = mk(dt.datetime(2026, 10, 28, 17, 15, tzinfo=utc), 'inbound', 0, self.brt, self.admin)
        # No created_by, so it should appear as "Unknown".
        self.c3 = mk(dt.datetime(2026, 10, 29, 6, 0, tzinfo=utc), 'outbound', 200, self.brt, None)
        # Another branch.
        self.c4 = mk(dt.datetime(2026, 10, 29, 7, 0, tzinfo=utc), 'outbound', 50, self.dng, self.dng_user,
                     notes='=HYPERLINK("http://evil.test","click")')

    # ---- summary ----
    def test_summary_totals_average_and_connect_rate(self):
        self.client.force_authenticate(self.admin)
        r = self.client.get('/api/calls/summary/?branch_id=%d' % self.brt.id)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['total_calls'], 3)
        self.assertEqual(r.data['inbound'], 1)
        self.assertEqual(r.data['outbound'], 2)
        self.assertEqual(r.data['total_seconds'], 300)
        self.assertEqual(r.data['total_formatted'], '00:05:00')
        self.assertEqual(r.data['avg_seconds'], 100)
        self.assertEqual(r.data['connected_calls'], 2)  # the 0-second call is not connected
        self.assertEqual(r.data['connect_rate'], 0.667)

    def test_summary_days_use_nepal_time(self):
        self.client.force_authenticate(self.admin)
        r = self.client.get('/api/calls/summary/?branch_id=%d' % self.brt.id)
        days = {d['date']: d for d in r.data['by_day']}
        self.assertEqual(days['2026-10-29']['calls'], 2)   # 20:00 UTC counts as Oct 29
        self.assertEqual(days['2026-10-28']['calls'], 1)   # 23:00 NPT stays on Oct 28

    def test_summary_unknown_caller(self):
        self.client.force_authenticate(self.admin)
        r = self.client.get('/api/calls/summary/?branch_id=%d' % self.brt.id)
        unknown = [u for u in r.data['by_user'] if u['user_id'] is None]
        self.assertEqual(len(unknown), 1)
        self.assertEqual(unknown[0]['name'], 'Unknown')

    def test_summary_empty_result_returns_zeros(self):
        self.client.force_authenticate(self.admin)
        r = self.client.get('/api/calls/summary/?call_timestamp__gte=2099-01-01')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['total_calls'], 0)
        self.assertEqual(r.data['total_seconds'], 0)
        self.assertEqual(r.data['avg_seconds'], 0)
        self.assertEqual(r.data['connect_rate'], 0)
        self.assertEqual(r.data['by_day'], [])
        self.assertEqual(r.data['by_user'], [])

    def test_summary_matches_list_count(self):
        self.client.force_authenticate(self.admin)
        qs = '?direction=outbound'
        listing = self.client.get('/api/calls/' + qs)
        summary = self.client.get('/api/calls/summary/' + qs)
        self.assertEqual(summary.data['total_calls'], listing.data['count'])

    # ---- export ----
    def _export_rows(self, qs=''):
        import csv
        import io
        r = self.client.get('/api/calls/export/' + qs)
        self.assertEqual(r.status_code, 200)
        body = b''.join(r.streaming_content)
        self.assertTrue(body.startswith(b'\xef\xbb\xbf'))  # UTF-8 BOM
        rows = list(csv.reader(io.StringIO(body.decode('utf-8-sig'))))
        return r, rows

    def test_export_content_type_header_and_rows(self):
        self.client.force_authenticate(self.admin)
        r, rows = self._export_rows()
        self.assertIn('text/csv', r['Content-Type'])
        self.assertRegex(r['Content-Disposition'], r'calls-\d{4}-\d{2}-\d{2}\.csv')
        self.assertEqual(rows[0], [
            'Date & time (NPT)', 'Direction', 'Lead', 'Reference', 'Phone',
            'Caller', 'Logged by', 'Duration (MM:SS)', 'Seconds',
            'Outcome', 'Notes', 'Recording',
        ])
        self.assertEqual(len(rows) - 1, 4)
        self.assertIn('Sita नेपाली', [row[2] for row in rows[1:]])

    def test_export_respects_filters(self):
        self.client.force_authenticate(self.admin)
        _, rows = self._export_rows('?direction=inbound')
        self.assertEqual(len(rows) - 1, 1)

    def test_export_neutralises_formula_notes(self):
        self.client.force_authenticate(self.admin)
        _, rows = self._export_rows()
        notes = [row[10] for row in rows[1:] if 'HYPERLINK' in row[10]]
        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0].startswith("'=HYPERLINK("))

    def test_export_query_count_is_fixed(self):
        import datetime as dt
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from apps.lead.models import LeadCall

        self.client.force_authenticate(self.admin)
        with CaptureQueriesContext(connection) as before:
            b''.join(self.client.get('/api/calls/export/').streaming_content)
        for i in range(10):
            LeadCall.objects.create(
                lead=self.lead,
                call_timestamp=dt.datetime(2026, 10, 30, 6, i, tzinfo=dt.timezone.utc),
                direction='outbound', caller='x', phone_number='1',
                duration_seconds=5, created_by=self.admin, branch=self.brt,
            )
        with CaptureQueriesContext(connection) as after:
            b''.join(self.client.get('/api/calls/export/').streaming_content)
        self.assertEqual(len(before), len(after))

    # ---- branch scoping (US-6) ----
    def test_non_staff_list_summary_export_stay_in_branch(self):
        self.client.force_authenticate(self.brt_user)
        listing = self.client.get('/api/calls/')
        self.assertEqual(listing.data['count'], 3)
        self.assertNotIn(self.c4.id, [r['id'] for r in listing.data['results']])

        summary = self.client.get('/api/calls/summary/')
        self.assertEqual(summary.data['total_calls'], 3)

        _, rows = self._export_rows()
        self.assertEqual(len(rows) - 1, 3)
        self.assertFalse(any('HYPERLINK' in row[10] for row in rows[1:]))

    def test_non_staff_cannot_widen_with_branch_id(self):
        self.client.force_authenticate(self.brt_user)
        r = self.client.get('/api/calls/summary/?branch_id=%d' % self.dng.id)
        self.assertEqual(r.data['total_calls'], 3)

    def test_staff_sees_all_and_can_narrow_by_branch(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get('/api/calls/summary/').data['total_calls'], 4)
        narrowed = self.client.get('/api/calls/summary/?branch_id=%d' % self.dng.id)
        self.assertEqual(narrowed.data['total_calls'], 1)