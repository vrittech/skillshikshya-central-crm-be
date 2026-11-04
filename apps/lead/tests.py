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
        self.lead = Lead.objects.create(
            full_name='Call Person', phone='9811111111', branch=self.branch,
        )
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

    def create_branch_call(
        self, branch, *, full_name, phone, caller='Other Counselor',
        direction='outbound', call_branch=None, timestamp=None, notes='',
    ):
        from django.utils import timezone as tz
        from apps.lead.models import LeadCall

        lead = Lead.objects.create(
            full_name=full_name, phone=phone, branch=branch,
        )
        call = LeadCall.objects.create(
            lead=lead,
            call_timestamp=timestamp or tz.now(),
            direction=direction,
            caller=caller,
            phone_number=phone,
            duration_seconds=90,
            notes=notes,
            created_by=self.admin,
            branch=call_branch or branch,
        )
        return call, lead

    def branch_a_user(self):
        return User.objects.create_user(
            email='branch-a@test.com', name='Branch A User', password='pass',
            branch=self.branch,
        )

    def test_non_staff_assigned_branch_sees_only_its_calls(self):
        from apps.branch.models import Branch
        branch_b = Branch.objects.create(name='Dharan', code='DHR')
        call_b, _ = self.create_branch_call(
            branch_b, full_name='Private Branch B Lead', phone='9899990001',
            caller='Private Branch B Counselor',
        )
        self.client.force_authenticate(self.branch_a_user())
        response = self.client.get('/api/calls/')
        rows = response.data['results']
        self.assertEqual(
            {row['id'] for row in rows},
            {self.recent_call.id, self.old_call.id},
        )
        self.assertNotIn(call_b.id, {row['id'] for row in rows})
        self.assertTrue(all(row['branch'] == self.branch.id for row in rows))

    def test_non_staff_cannot_override_branch_with_branch_id(self):
        from apps.branch.models import Branch
        branch_b = Branch.objects.create(name='Dharan', code='DHR')
        call_b, _ = self.create_branch_call(
            branch_b, full_name='Private Branch B Lead', phone='9899990002',
        )
        self.client.force_authenticate(self.branch_a_user())
        response = self.client.get(f'/api/calls/?branch_id={branch_b.id}')
        ids = {row['id'] for row in response.data['results']}
        self.assertEqual(ids, {self.recent_call.id, self.old_call.id})
        self.assertNotIn(call_b.id, ids)

    def test_search_filters_and_pagination_keep_branch_scope(self):
        from datetime import timedelta
        from apps.branch.models import Branch
        from apps.student.models import Student
        from django.utils import timezone as tz

        branch_b = Branch.objects.create(name='Dharan', code='DHR')
        timestamp = tz.now() + timedelta(minutes=1)
        call_b, lead_b = self.create_branch_call(
            branch_b,
            full_name='Private Branch B Lead',
            phone='9899990003',
            caller='Private Branch B Counselor',
            direction='inbound',
            timestamp=timestamp,
            notes='Branch B confidential notes',
        )
        student_b = Student.objects.create(
            full_name='Private Branch B Student',
            phone='9899990003',
            lead=lead_b,
        )
        for index in range(11):
            self.create_branch_call(
                branch_b,
                full_name=f'Other Branch B Lead {index}',
                phone=f'989999{index:04d}',
                timestamp=timestamp + timedelta(seconds=index + 1),
            )

        self.client.force_authenticate(self.branch_a_user())
        searched = self.client.get('/api/calls/?search=Branch B confidential')
        self.assertEqual(searched.data['count'], 0)
        by_student = self.client.get(f'/api/calls/?student={student_b.id}')
        self.assertEqual(by_student.data['count'], 0)
        by_direction = self.client.get('/api/calls/?direction=inbound')
        self.assertEqual(
            [row['id'] for row in by_direction.data['results']],
            [self.old_call.id],
        )
        by_ordering = self.client.get('/api/calls/?ordering=duration_seconds')
        self.assertTrue(
            {row['id'] for row in by_ordering.data['results']}.issubset(
                {self.recent_call.id, self.old_call.id},
            )
        )

        second_page = self.client.get('/api/calls/?page=2&page_size=1')
        self.assertEqual(second_page.data['count'], 2)
        self.assertEqual(
            [row['id'] for row in second_page.data['results']],
            [self.old_call.id],
        )
        self.assertNotIn(call_b.id, [row['id'] for row in second_page.data['results']])

    def test_related_lead_from_other_branch_is_not_exposed(self):
        from apps.branch.models import Branch
        branch_b = Branch.objects.create(name='Dharan', code='DHR')
        mismatched_call, _ = self.create_branch_call(
            branch_b,
            full_name='Private Mismatched Lead',
            phone='9899990004',
            call_branch=self.branch,
        )
        self.client.force_authenticate(self.branch_a_user())
        response = self.client.get('/api/calls/')
        rows = response.data['results']
        self.assertNotIn(mismatched_call.id, {row['id'] for row in rows})
        self.assertNotIn(
            'Private Mismatched Lead',
            {row['lead_name'] for row in rows},
        )

    def test_staff_sees_all_branches_by_default_and_can_filter(self):
        from apps.branch.models import Branch
        branch_b = Branch.objects.create(name='Dharan', code='DHR')
        call_b, _ = self.create_branch_call(
            branch_b, full_name='Branch B Lead', phone='9899990005',
        )

        response = self.client.get('/api/calls/')
        self.assertEqual(
            {row['id'] for row in response.data['results']},
            {self.recent_call.id, self.old_call.id, call_b.id},
        )
        filtered = self.client.get(f'/api/calls/?branch_id={branch_b.id}')
        self.assertEqual(
            [row['id'] for row in filtered.data['results']],
            [call_b.id],
        )

    def test_non_staff_without_branch_keeps_existing_unrestricted_behavior(self):
        from apps.branch.models import Branch
        branch_b = Branch.objects.create(name='Dharan', code='DHR')
        call_b, _ = self.create_branch_call(
            branch_b, full_name='Branch B Lead', phone='9899990006',
        )
        unassigned = User.objects.create_user(
            email='unassigned@test.com', name='Unassigned User', password='pass',
        )
        self.client.force_authenticate(unassigned)
        response = self.client.get('/api/calls/')
        self.assertEqual(
            {row['id'] for row in response.data['results']},
            {self.recent_call.id, self.old_call.id, call_b.id},
        )


class GlobalCallExportTests(APITestCase):
    def setUp(self):
        from apps.branch.models import Branch

        self.admin = User.objects.create_user(
            email='csv-admin@test.com', name='CSV Admin', password='pass',
            is_staff=True,
        )
        self.branch_a = Branch.objects.create(name='CSV Branch A', code='CVA')
        self.branch_b = Branch.objects.create(name='CSV Branch B', code='CVB')
        self.lead_a = Lead.objects.create(
            full_name='नेपाली Lead', phone='9811111111', branch=self.branch_a,
        )
        self.lead_b = Lead.objects.create(
            full_name='Private Branch Lead', phone='9822222222', branch=self.branch_b,
        )
        self.call_a = self.create_call(
            self.lead_a,
            self.branch_a,
            caller='CSV Counselor',
            phone='9811111111',
            notes='नेपाली टिप्पणी',
        )
        self.call_b = self.create_call(
            self.lead_b,
            self.branch_b,
            caller='Other Counselor',
            phone='9822222222',
            notes='Private note',
        )
        self.client.force_authenticate(self.admin)

    def create_call(
        self, lead, branch, *, caller, phone, notes='', timestamp=None,
        direction='outbound', duration=259, outcome=None, recording_url=None,
        created_by=None,
    ):
        from django.utils import timezone as tz
        from apps.lead.models import LeadCall

        return LeadCall.objects.create(
            lead=lead,
            branch=branch,
            call_timestamp=timestamp or tz.now(),
            direction=direction,
            caller=caller,
            phone_number=phone,
            duration_seconds=duration,
            outcome=outcome,
            notes=notes,
            recording_url=recording_url,
            created_by=created_by or self.admin,
        )

    def get_csv(self, url='/api/calls/export-csv/'):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = b''.join(response.streaming_content)
        self.assertTrue(content.startswith(b'\xef\xbb\xbf'))
        return response, content.decode('utf-8-sig')

    def test_csv_has_exact_columns_and_download_filename(self):
        import csv
        from io import StringIO
        from zoneinfo import ZoneInfo

        from django.utils import timezone as tz

        response, content = self.get_csv()
        rows = list(csv.reader(StringIO(content)))
        self.assertEqual(rows[0], [
            'Date & time (NPT)', 'Direction', 'Lead', 'Reference', 'Phone',
            'Caller', 'Logged by', 'Duration (MM)', 'Seconds', 'Outcome',
            'Notes', 'Recording',
        ])
        self.assertEqual(
            response['Content-Disposition'],
            'attachment; filename="calls-'
            f'{tz.localtime(tz.now(), ZoneInfo("Asia/Kathmandu")).strftime("%Y-%m-%d")}.csv"',
        )
        self.assertTrue(response['Content-Type'].startswith('text/csv'))

    def test_export_contains_every_matching_call_without_page_limit(self):
        import csv
        from io import StringIO

        for index in range(25):
            self.create_call(
                self.lead_a,
                self.branch_a,
                caller=f'Caller {index}',
                phone=f'981111{index:04d}',
            )
        _, content = self.get_csv('/api/calls/export-csv/?page=2&page_size=1')
        rows = list(csv.reader(StringIO(content)))
        self.assertEqual(len(rows) - 1, 27)

    def test_export_applies_search_and_direction_filters(self):
        import csv
        from io import StringIO

        _, content = self.get_csv(
            '/api/calls/export-csv/?search=CSV%20Counselor&direction=outbound',
        )
        rows = list(csv.reader(StringIO(content)))
        self.assertEqual(len(rows) - 1, 1)
        self.assertEqual(rows[1][5], 'CSV Counselor')

        _, content = self.get_csv('/api/calls/export-csv/?direction=inbound')
        rows = list(csv.reader(StringIO(content)))
        self.assertEqual(len(rows) - 1, 0)

    def test_export_applies_logged_by_and_date_range_filters(self):
        import csv
        from datetime import datetime, timezone as datetime_timezone
        from io import StringIO

        other_user = User.objects.create_user(
            email='csv-other@test.com', name='Other Logger', password='pass',
        )
        self.create_call(
            self.lead_a,
            self.branch_a,
            caller='Old call',
            phone='9811111212',
            timestamp=datetime(
                2020, 1, 1, tzinfo=datetime_timezone.utc,
            ),
            created_by=other_user,
        )

        _, content = self.get_csv(
            f'/api/calls/export-csv/?created_by={other_user.id}'
            '&call_timestamp__gte=2019-01-01T00:00:00Z'
            '&call_timestamp__lte=2021-01-01T00:00:00Z',
        )
        rows = list(csv.reader(StringIO(content)))
        self.assertEqual(len(rows) - 1, 1)
        self.assertEqual(rows[1][5], 'Old call')
        self.assertEqual(rows[1][6], 'Other Logger')

    def test_export_formats_npt_duration_empty_fields_and_nepali_text(self):
        import csv
        from datetime import datetime, timezone as datetime_timezone
        from io import StringIO
        from zoneinfo import ZoneInfo

        call = self.call_a
        call.call_timestamp = datetime(
            2026, 8, 26, 17, 15, tzinfo=datetime_timezone.utc,
        )
        call.outcome = None
        call.notes = 'नेपाली टिप्पणी'
        call.recording_url = None
        call.duration_seconds = 259
        call.save()

        _, content = self.get_csv()
        row = next(
            row for row in csv.reader(StringIO(content))
            if row[5] == 'CSV Counselor'
        )
        self.assertEqual(row[0], '2026-08-26 23:00:00')
        self.assertEqual(row[1], 'Outbound')
        self.assertEqual(row[2], 'नेपाली Lead')
        self.assertEqual(row[4], '9811111111')
        self.assertEqual(row[6], 'CSV Admin')
        self.assertEqual(row[7], '4.32')
        self.assertEqual(row[8], '259')
        self.assertEqual(row[9], '')
        self.assertEqual(row[10], 'नेपाली टिप्पणी')
        self.assertEqual(row[11], '')

    def test_formula_like_text_is_escaped(self):
        import csv
        from io import StringIO

        self.call_a.notes = '=HYPERLINK("https://example.com")'
        self.call_a.caller = '+SUM(1,1)'
        self.call_a.save()
        _, content = self.get_csv()
        row = next(
            row for row in csv.reader(StringIO(content))
            if row[2] == 'नेपाली Lead'
        )
        self.assertEqual(row[5], "'+SUM(1,1)")
        self.assertEqual(row[10], "'=HYPERLINK(\"https://example.com\")")

    def test_branch_user_cannot_export_other_branch_with_branch_filter(self):
        import csv
        from io import StringIO

        branch_user = User.objects.create_user(
            email='csv-branch@test.com', name='CSV Branch User', password='pass',
            branch=self.branch_a,
        )
        self.client.force_authenticate(branch_user)
        _, content = self.get_csv(
            f'/api/calls/export-csv/?branch_id={self.branch_b.id}',
        )
        rows = list(csv.reader(StringIO(content)))
        self.assertEqual(len(rows) - 1, 1)
        self.assertEqual(rows[1][2], 'नेपाली Lead')
        self.assertNotIn('Private Branch Lead', content)

    def test_staff_can_filter_export_by_branch(self):
        import csv
        from io import StringIO

        _, content = self.get_csv(
            f'/api/calls/export-csv/?branch_id={self.branch_b.id}',
        )
        rows = list(csv.reader(StringIO(content)))
        self.assertEqual(len(rows) - 1, 1)
        self.assertEqual(rows[1][2], 'Private Branch Lead')

    def test_unauthenticated_user_cannot_export_calls(self):
        self.client.force_authenticate(None)
        response = self.client.get('/api/calls/export-csv/')
        self.assertEqual(response.status_code, 401)


class GlobalCallSummaryTests(APITestCase):
    summary_url = '/api/calls/summary/'

    def setUp(self):
        from datetime import timedelta
        from django.utils import timezone as tz
        from apps.branch.models import Branch
        from apps.lead.models import LeadCall
        from apps.student.models import Student

        self.admin = User.objects.create_user(
            email='summary-admin@test.com', name='Summary Admin',
            password='pass', is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.branch = Branch.objects.create(name='Summary Branch', code='SUM')
        lead = Lead.objects.create(
            full_name='Summary Test Lead',
            phone='9811111100',
            branch=self.branch,
        )
        self.student = Student.objects.create(
            full_name='Summary Test Student',
            phone='9811111100',
            lead=lead,
        )
        now = tz.now()
        self.recent_call = LeadCall.objects.create(
            lead=lead,
            call_timestamp=now,
            direction='outbound',
            caller='Summary Counselor',
            phone_number='9811111100',
            duration_seconds=270,
            outcome='interested',
            created_by=self.admin,
            branch=self.branch,
        )
        self.old_call = LeadCall.objects.create(
            lead=lead,
            call_timestamp=now - timedelta(days=10),
            direction='inbound',
            caller='Other Counselor',
            phone_number='9822222200',
            duration_seconds=60,
            created_by=self.admin,
            branch=self.branch,
        )

    def create_branch_call(
        self, branch, *, full_name, phone, caller='Other Counselor',
        direction='outbound', call_branch=None, timestamp=None, notes='',
    ):
        from django.utils import timezone as tz
        from apps.lead.models import LeadCall

        lead = Lead.objects.create(
            full_name=full_name, phone=phone, branch=branch,
        )
        call = LeadCall.objects.create(
            lead=lead,
            call_timestamp=timestamp or tz.now(),
            direction=direction,
            caller=caller,
            phone_number=phone,
            duration_seconds=90,
            notes=notes,
            created_by=self.admin,
            branch=call_branch or branch,
        )
        return call, lead

    def branch_a_user(self):
        return User.objects.create_user(
            email='summary-branch-a@test.com',
            name='Summary Branch A User',
            password='pass',
            branch=self.branch,
        )

    def test_summary_aggregates_total_directions_talk_time_average_and_connect_rate(self):
        response = self.client.get(self.summary_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total_calls'], 2)
        self.assertEqual(response.data['inbound_calls'], 1)
        self.assertEqual(response.data['outbound_calls'], 1)
        self.assertEqual(response.data['total_duration_seconds'], 330)
        self.assertEqual(response.data['average_duration_seconds'], 165)
        self.assertEqual(response.data['connected_calls'], 2)
        self.assertEqual(response.data['connect_rate'], 100)

    def test_summary_handles_no_matching_calls(self):
        response = self.client.get(f'{self.summary_url}?created_by=999999')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total_calls'], 0)
        self.assertEqual(response.data['inbound_calls'], 0)
        self.assertEqual(response.data['outbound_calls'], 0)
        self.assertEqual(response.data['total_duration_seconds'], 0)
        self.assertEqual(response.data['average_duration_seconds'], 0)
        self.assertEqual(response.data['connected_calls'], 0)
        self.assertEqual(response.data['connect_rate'], 0)
        self.assertEqual(response.data['calls_per_day'], [])
        self.assertEqual(response.data['top_callers'], [])

    def test_zero_duration_calls_are_not_connected(self):
        from apps.lead.models import LeadCall
        LeadCall.objects.filter(pk=self.old_call.pk).update(duration_seconds=0)

        response = self.client.get(self.summary_url)
        self.assertEqual(response.data['connected_calls'], 1)
        self.assertEqual(response.data['connect_rate'], 50)

    def test_calls_per_day_uses_nepal_calendar_date(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from apps.lead.models import LeadCall

        nepal = ZoneInfo('Asia/Kathmandu')
        late_call, _ = self.create_branch_call(
            self.branch,
            full_name='Nepal late evening lead',
            phone='9800001001',
            timestamp=datetime(2026, 8, 26, 23, 0, tzinfo=nepal),
        )
        early_call, _ = self.create_branch_call(
            self.branch,
            full_name='Nepal next day lead',
            phone='9800001002',
            timestamp=datetime(2026, 8, 27, 0, 15, tzinfo=nepal),
        )

        response = self.client.get(self.summary_url)
        daily = {row['date']: row['count'] for row in response.data['calls_per_day']}
        self.assertEqual(daily['2026-08-26'], 1)
        self.assertEqual(daily['2026-08-27'], 1)
        self.assertIn(late_call.id, LeadCall.objects.values_list('id', flat=True))
        self.assertIn(early_call.id, LeadCall.objects.values_list('id', flat=True))

    def test_top_callers_returns_ten_users_in_descending_count_order(self):
        from apps.account.models import User

        caller_users = []
        for user_index in range(12):
            user = User.objects.create_user(
                email=f'caller{user_index}@test.com',
                name=f'Caller {user_index}',
                password='pass',
            )
            caller_users.append(user)
            for call_index in range(12 - user_index):
                call, _ = self.create_branch_call(
                    self.branch,
                    full_name=f'Call lead {user_index}-{call_index}',
                    phone=f'98001{user_index:02d}{call_index:03d}',
                )
                call.created_by = user
                call.save(update_fields=['created_by'])

        response = self.client.get(self.summary_url)
        callers = response.data['top_callers']
        self.assertEqual(len(callers), 10)
        self.assertEqual(
            [caller['call_count'] for caller in callers],
            list(range(12, 2, -1)),
        )
        self.assertEqual(callers[0]['id'], caller_users[0].id)
        self.assertEqual(callers[0]['name'], caller_users[0].name)
        self.assertEqual(callers[0]['email'], caller_users[0].email)

    def test_missing_created_by_is_reported_as_unknown_caller(self):
        from apps.lead.models import LeadCall
        LeadCall.objects.filter(pk=self.recent_call.pk).update(created_by=None)

        response = self.client.get(self.summary_url)
        unknown = next(
            caller for caller in response.data['top_callers']
            if caller['id'] is None
        )
        self.assertEqual(unknown['name'], 'Unknown caller')
        self.assertEqual(unknown['email'], None)

    def test_summary_filters_match_global_call_list(self):
        filters = (
            f'?search=Summary Test Lead&direction=outbound'
            f'&created_by={self.admin.id}&student={self.student.id}'
        )
        summary = self.client.get(f'{self.summary_url}{filters}')
        call_list = self.client.get(f'/api/calls/{filters}&page_size=100')
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.data['total_calls'], call_list.data['count'])
        self.assertEqual(summary.data['total_calls'], 1)

    def test_summary_date_filter_matches_global_call_list(self):
        from urllib.parse import urlencode

        date_filter = '?' + urlencode({
            'call_timestamp__gte': self.recent_call.call_timestamp.isoformat(),
        })
        summary = self.client.get(f'{self.summary_url}{date_filter}')
        call_list = self.client.get(f'/api/calls/{date_filter}&page_size=100')
        self.assertEqual(summary.data['total_calls'], call_list.data['count'])
        self.assertEqual(summary.data['total_calls'], 1)

    def test_staff_branch_filter_applies_to_summary(self):
        from apps.branch.models import Branch
        branch_b = Branch.objects.create(name='Dharan', code='DHR')
        call_b, _ = self.create_branch_call(
            branch_b, full_name='Branch B Summary Lead', phone='9800001010',
        )

        response = self.client.get(f'{self.summary_url}?branch_id={branch_b.id}')
        self.assertEqual(response.data['total_calls'], 1)
        self.assertEqual(response.data['total_duration_seconds'], 90)
        self.assertEqual(response.data['calls_per_day'][0]['count'], 1)
        self.assertEqual(response.data['top_callers'][0]['call_count'], 1)
        self.assertEqual(call_b.branch_id, branch_b.id)

    def test_non_staff_cannot_read_other_branch_summary(self):
        from apps.branch.models import Branch
        branch_b = Branch.objects.create(name='Dharan', code='DHR')
        self.create_branch_call(
            branch_b, full_name='Private Branch Summary Lead', phone='9800001011',
        )
        self.client.force_authenticate(self.branch_a_user())

        response = self.client.get(
            f'{self.summary_url}?branch_id={branch_b.id}&search=Private Branch',
        )
        self.assertEqual(response.data['total_calls'], 0)
        self.assertEqual(response.data['calls_per_day'], [])
        self.assertEqual(response.data['top_callers'], [])

    def test_summary_uses_a_fixed_number_of_aggregate_queries(self):
        with self.assertNumQueries(3):
            response = self.client.get(self.summary_url)
        self.assertEqual(response.status_code, 200)
