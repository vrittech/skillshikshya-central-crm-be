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


def _npt(year, month, day, hour=0, minute=0, second=0):
    """Aware datetime in Nepal time (UTC+05:45)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime(year, month, day, hour, minute, second, tzinfo=ZoneInfo('Asia/Kathmandu'))


class CallFixtureMixin:
    """Two branches, a manager in each, calls on both sides of NPT midnight."""

    def make_call(self, **kwargs):
        from apps.lead.models import LeadCall
        defaults = {
            'lead': self.lead, 'call_timestamp': _npt(2026, 11, 10, 12),
            'direction': 'outbound', 'caller': 'Sita', 'phone_number': '9811111111',
            'duration_seconds': 60, 'created_by': self.sita, 'branch': self.brt,
        }
        defaults.update(kwargs)
        return LeadCall.objects.create(**defaults)

    def setUp(self):
        from apps.branch.models import Branch
        self.brt = Branch.objects.create(name='Biratnagar', code='BRT')
        self.dhr = Branch.objects.create(name='Dharan', code='DHR')
        self.admin = User.objects.create_user(
            email='admin@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.sita = User.objects.create_user(
            email='sita@test.com', name='Sita', password='pass', branch=self.brt,
        )
        self.ram = User.objects.create_user(
            email='ram@test.com', name='Ram', password='pass', branch=self.brt,
        )
        self.hari = User.objects.create_user(
            email='hari@test.com', name='Hari', password='pass', branch=self.dhr,
        )
        self.lead = Lead.objects.create(full_name='सीता कुमारी', phone='9811111111')
        self.other_lead = Lead.objects.create(full_name='Dharan Lead', phone='9822222222')

        # Biratnagar: 23:00 NPT on the 10th belongs to the 10th, 00:30 NPT on
        # the 11th belongs to the 11th even though both are 10 Nov in UTC.
        self.late_10th = self.make_call(
            call_timestamp=_npt(2026, 11, 10, 23, 0), duration_seconds=120,
        )
        self.early_11th = self.make_call(
            call_timestamp=_npt(2026, 11, 11, 0, 30), direction='inbound',
            duration_seconds=0, created_by=self.ram, caller='Ram',
        )
        self.last_second_11th = self.make_call(
            call_timestamp=_npt(2026, 11, 11, 23, 59, 59), duration_seconds=259,
            notes='=HYPERLINK("http://evil.example","click")',
        )
        # Dharan: must never leak to Biratnagar users.
        self.dharan_call = self.make_call(
            lead=self.other_lead, branch=self.dhr, created_by=self.hari,
            caller='Hari', phone_number='9822222222', duration_seconds=300,
            call_timestamp=_npt(2026, 11, 11, 10),
        )


class CallSummaryTests(CallFixtureMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.admin)

    def test_totals_split_talk_time_and_connect_rate(self):
        data = self.client.get('/api/calls/summary/', {'branch_id': self.brt.id}).data
        self.assertEqual(data['total_calls'], 3)
        self.assertEqual(data['inbound_calls'], 1)
        self.assertEqual(data['outbound_calls'], 2)
        self.assertEqual(data['talk_time_seconds'], 120 + 0 + 259)
        self.assertEqual(data['avg_duration_seconds'], round(379 / 3))
        # 0-second call is not connected: 2 of 3.
        self.assertEqual(data['connected_calls'], 2)
        self.assertEqual(data['connect_rate'], 66.7)

    def test_days_are_bucketed_in_nepal_time(self):
        data = self.client.get('/api/calls/summary/', {'branch_id': self.brt.id}).data
        self.assertEqual(data['per_day'], [
            {'date': '2026-11-10', 'count': 1},
            {'date': '2026-11-11', 'count': 2},
        ])

    def test_per_day_fills_empty_days_in_the_filtered_range(self):
        data = self.client.get('/api/calls/summary/', {
            'branch_id': self.brt.id,
            'call_timestamp__gte': '2026-11-09', 'call_timestamp__lte': '2026-11-12',
        }).data
        self.assertEqual(
            [(d['date'], d['count']) for d in data['per_day']],
            [('2026-11-09', 0), ('2026-11-10', 1), ('2026-11-11', 2), ('2026-11-12', 0)],
        )

    def test_top_callers_ordered_by_call_count(self):
        data = self.client.get('/api/calls/summary/').data
        names = [(c['name'], c['calls']) for c in data['top_callers']]
        self.assertEqual(names[0], ('Sita', 2))
        self.assertEqual(set(names[1:]), {('Ram', 1), ('Hari', 1)})

    def test_total_matches_list_count_for_same_filters(self):
        params = {'direction': 'outbound', 'search': 'Sita', 'call_timestamp__gte': '2026-11-11'}
        listed = self.client.get('/api/calls/', params).data['count']
        summary = self.client.get('/api/calls/summary/', params).data['total_calls']
        self.assertEqual(listed, 1)
        self.assertEqual(summary, listed)

    def test_no_matching_calls_returns_zeros(self):
        data = self.client.get('/api/calls/summary/', {'search': 'nobody-matches-this'}).data
        self.assertEqual(data['total_calls'], 0)
        self.assertEqual(data['talk_time_seconds'], 0)
        self.assertEqual(data['avg_duration_seconds'], 0)
        self.assertEqual(data['connect_rate'], 0)
        self.assertEqual(data['per_day'], [])
        self.assertEqual(data['top_callers'], [])

    def test_summary_query_count_does_not_grow_with_rows(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as small:
            self.client.get('/api/calls/summary/')
        for i in range(25):
            self.make_call(call_timestamp=_npt(2026, 11, 1 + i % 9, 9))
        with CaptureQueriesContext(connection) as large:
            self.client.get('/api/calls/summary/')
        self.assertEqual(len(small), len(large))


class CallDateFilterTests(CallFixtureMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.admin)

    def ids(self, **params):
        response = self.client.get('/api/calls/', {'branch_id': self.brt.id, **params})
        self.assertEqual(response.status_code, 200)
        return {r['id'] for r in response.data['results']}

    def test_single_day_includes_up_to_23_59_59_npt(self):
        got = self.ids(call_timestamp__gte='2026-11-11', call_timestamp__lte='2026-11-11')
        self.assertEqual(got, {self.early_11th.id, self.last_second_11th.id})

    def test_previous_day_excludes_calls_after_npt_midnight(self):
        got = self.ids(call_timestamp__gte='2026-11-10', call_timestamp__lte='2026-11-10')
        self.assertEqual(got, {self.late_10th.id})

    def test_full_iso_datetime_still_works(self):
        got = self.ids(call_timestamp__gte='2026-11-11T00:00:00+05:45',
                       call_timestamp__lte='2026-11-11T12:00:00+05:45')
        self.assertEqual(got, {self.early_11th.id})

    def test_invalid_date_is_a_400(self):
        response = self.client.get('/api/calls/', {'call_timestamp__gte': 'yesterday'})
        self.assertEqual(response.status_code, 400)


class CallExportTests(CallFixtureMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.admin)

    def export(self, **params):
        response = self.client.get('/api/calls/export_csv/', params)
        self.assertEqual(response.status_code, 200)
        body = b''.join(response.streaming_content).decode('utf-8')
        return response, body

    def rows(self, body):
        import csv
        import io
        return list(csv.reader(io.StringIO(body.lstrip('﻿'))))

    def test_filename_bom_and_header(self):
        response, body = self.export()
        self.assertRegex(response['Content-Disposition'], r'filename="calls-\d{4}-\d{2}-\d{2}\.csv"')
        self.assertTrue(body.startswith('﻿'))
        self.assertEqual(self.rows(body)[0], [
            'Date & time (NPT)', 'Direction', 'Lead', 'Reference', 'Phone', 'Caller',
            'Logged by', 'Duration (MM:SS)', 'Seconds', 'Outcome', 'Notes', 'Recording',
        ])

    def test_rows_use_npt_time_and_mm_ss_and_keep_devanagari(self):
        _, body = self.export(branch_id=self.brt.id, ordering='call_timestamp')
        first = self.rows(body)[1]
        self.assertEqual(first[0], '2026-11-10 23:00:00')
        self.assertEqual(first[1], 'Outbound')
        self.assertEqual(first[2], 'सीता कुमारी')
        self.assertEqual(first[6], 'Sita')
        self.assertEqual(first[7:9], ['02:00', '120'])

    def test_exports_every_page_matching_the_filters(self):
        for i in range(30):
            self.make_call(call_timestamp=_npt(2026, 11, 5, 9, i))
        _, body = self.export(branch_id=self.brt.id)
        listed = self.client.get('/api/calls/', {'branch_id': self.brt.id}).data['count']
        self.assertEqual(len(self.rows(body)) - 1, listed)
        self.assertEqual(listed, 33)

    def test_formula_cells_are_neutralised(self):
        from apps.lead.calls import csv_safe
        _, body = self.export(search='HYPERLINK')
        notes = self.rows(body)[1][10]
        self.assertEqual(notes, '\'=HYPERLINK("http://evil.example","click")')
        for dangerous in ['=1+1', '+1', '-1', '@SUM(A1)']:
            self.assertTrue(csv_safe(dangerous).startswith("'"))
        self.assertEqual(csv_safe('plain note'), 'plain note')

    def test_export_query_count_does_not_grow_with_rows(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as small:
            self.export()
        for i in range(40):
            self.make_call(call_timestamp=_npt(2026, 11, 6, 9, i))
        with CaptureQueriesContext(connection) as large:
            self.export()
        self.assertEqual(len(small), len(large))


class CallBranchScopingTests(CallFixtureMixin, APITestCase):
    """US-6: a non-staff user only ever sees their own branch's calls."""

    def test_list_only_shows_own_branch(self):
        self.client.force_authenticate(self.hari)
        ids = {r['id'] for r in self.client.get('/api/calls/').data['results']}
        self.assertEqual(ids, {self.dharan_call.id})

    def test_non_staff_cannot_widen_scope_with_branch_id(self):
        self.client.force_authenticate(self.hari)
        ids = {r['id'] for r in self.client.get('/api/calls/', {'branch_id': self.brt.id}).data['results']}
        self.assertEqual(ids, {self.dharan_call.id})

    def test_summary_only_counts_own_branch(self):
        self.client.force_authenticate(self.hari)
        response = self.client.get('/api/calls/summary/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total_calls'], 1)
        self.assertEqual([c['name'] for c in response.data['top_callers']], ['Hari'])

    def test_export_only_contains_own_branch(self):
        self.client.force_authenticate(self.sita)
        response = self.client.get('/api/calls/export_csv/')
        self.assertEqual(response.status_code, 200)
        body = b''.join(response.streaming_content).decode('utf-8')
        self.assertNotIn('9822222222', body)
        self.assertNotIn('Dharan Lead', body)
        self.assertEqual(body.count('\n') - 1, 3)

    def test_staff_sees_all_branches_and_can_narrow(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get('/api/calls/').data['count'], 4)
        self.assertEqual(self.client.get('/api/calls/summary/').data['total_calls'], 4)
        narrowed = self.client.get('/api/calls/', {'branch_id': self.dhr.id}).data
        self.assertEqual([r['id'] for r in narrowed['results']], [self.dharan_call.id])

    def test_list_query_count_does_not_grow_with_rows(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        self.client.force_authenticate(self.admin)
        with CaptureQueriesContext(connection) as small:
            self.client.get('/api/calls/', {'page_size': 20})
        for i in range(25):
            self.make_call(call_timestamp=_npt(2026, 11, 7, 9, i))
        with CaptureQueriesContext(connection) as large:
            self.client.get('/api/calls/', {'page_size': 20})
        self.assertEqual(len(small), len(large))

    def test_branchless_non_staff_user_today_sees_every_branch(self):
        """
        Documents current behaviour for the US-6 write-up (not changed in this
        PR): BranchFilterBackend falls through to the unfiltered queryset when
        a non-staff user has no branch. Flip this test when that is fixed.
        """
        nobody = User.objects.create_user(email='nb@test.com', name='No Branch', password='x')
        self.client.force_authenticate(nobody)
        self.assertEqual(self.client.get('/api/calls/').data['count'], 4)
