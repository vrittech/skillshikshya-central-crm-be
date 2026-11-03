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

    def test_list_returns_duration_formatted(self):
        response = self.client.get('/api/calls/')
        self.assertEqual(response.status_code, 200)
        row = response.data['results'][0]
        self.assertEqual(row['duration_seconds'], 270)
        self.assertEqual(row['duration_formatted'], '04:30')


class GlobalCallSummaryTests(APITestCase):
    def setUp(self):
        from datetime import datetime, timezone as dt_tz
        from apps.branch.models import Branch
        from apps.lead.models import LeadCall

        self.branch = Branch.objects.create(name='Kathmandu', code='KTM')
        self.admin = User.objects.create_user(
            email='admin@calltest.com', name='Admin User', password='p', is_staff=True,
        )
        self.user1 = User.objects.create_user(
            email='u1@calltest.com', name='User One', password='p',
        )
        self.user2 = User.objects.create_user(
            email='u2@calltest.com', name='User Two', password='p',
        )
        self.client.force_authenticate(self.admin)
        self.lead = Lead.objects.create(full_name='Summary Lead', phone='9841000000', branch=self.branch)

        # Oct 28, 2026 at 20:00:00 UTC -> in Nepal (+05:45) is Oct 29, 2026 at 01:45:00
        dt_nepal_boundary = datetime(2026, 10, 28, 20, 0, 0, tzinfo=dt_tz.utc)

        # Call 1: user1, inbound, 120s, on dt_nepal_boundary (belongs to Oct 29 in Nepal)
        self.call1 = LeadCall.objects.create(
            lead=self.lead, call_timestamp=dt_nepal_boundary,
            direction='inbound', caller='Client A', phone_number='9841000000',
            duration_seconds=120, outcome='Interested', created_by=self.user1, branch=self.branch,
        )
        # Call 2: user1, outbound, 180s, on Oct 29 at 10:00:00 UTC (Oct 29 in Nepal)
        self.call2 = LeadCall.objects.create(
            lead=self.lead, call_timestamp=datetime(2026, 10, 29, 10, 0, 0, tzinfo=dt_tz.utc),
            direction='outbound', caller='User One', phone_number='9841000000',
            duration_seconds=180, outcome='Enrolled', created_by=self.user1, branch=self.branch,
        )
        # Call 3: user2, outbound, 0s (unconnected), Oct 28 at 10:00:00 UTC (Oct 28 in Nepal)
        self.call3 = LeadCall.objects.create(
            lead=self.lead, call_timestamp=datetime(2026, 10, 28, 10, 0, 0, tzinfo=dt_tz.utc),
            direction='outbound', caller='User Two', phone_number='9841000001',
            duration_seconds=0, outcome='No answer', created_by=self.user2, branch=self.branch,
        )
        # Call 4: created_by=None (unknown caller), inbound, 60s, Oct 28 at 12:00:00 UTC (Oct 28 in Nepal)
        self.call4 = LeadCall.objects.create(
            lead=self.lead, call_timestamp=datetime(2026, 10, 28, 12, 0, 0, tzinfo=dt_tz.utc),
            direction='inbound', caller='Unknown Inbound', phone_number='9841000002',
            duration_seconds=60, outcome='Inquiry', created_by=None, branch=self.branch,
        )

    def test_summary_statistics_and_nepal_tz_grouping(self):
        response = self.client.get('/api/calls/summary/')
        self.assertEqual(response.status_code, 200)
        data = response.data

        # 4 total calls: 2 inbound, 2 outbound
        self.assertEqual(data['total_calls'], 4)
        self.assertEqual(data['inbound'], 2)
        self.assertEqual(data['outbound'], 2)

        # Durations: 120 + 180 + 0 + 60 = 360 seconds
        self.assertEqual(data['total_seconds'], 360)
        # 360 seconds = 00:06:00
        self.assertEqual(data['total_formatted'], '00:06:00')
        # avg = 360 / 4 = 90 seconds
        self.assertEqual(data['avg_seconds'], 90)

        # Connected calls (duration > 0): call1 (120), call2 (180), call4 (60) = 3 calls
        self.assertEqual(data['connected_calls'], 3)
        # connect_rate = 3 / 4 = 0.75
        self.assertEqual(data['connect_rate'], 0.75)

        # by_day:
        # Call 3 (Oct 28 10:00 UTC) -> 2026-10-28 (0s)
        # Call 4 (Oct 28 12:00 UTC) -> 2026-10-28 (60s)
        # -> 2026-10-28: 2 calls, 60s
        # Call 1 (Oct 28 20:00 UTC) -> 2026-10-29 in Nepal! (120s)
        # Call 2 (Oct 29 10:00 UTC) -> 2026-10-29 in Nepal! (180s)
        # -> 2026-10-29: 2 calls, 300s
        by_day_map = {d['date']: d for d in data['by_day']}
        self.assertIn('2026-10-28', by_day_map)
        self.assertIn('2026-10-29', by_day_map)
        self.assertEqual(by_day_map['2026-10-28']['calls'], 2)
        self.assertEqual(by_day_map['2026-10-28']['seconds'], 60)
        self.assertEqual(by_day_map['2026-10-29']['calls'], 2)
        self.assertEqual(by_day_map['2026-10-29']['seconds'], 300)

        # by_user:
        # user1: 2 calls, 300s
        # user2: 1 call, 0s
        # created_by=None: user_id: null, name: "Unknown", 1 call, 60s
        by_user = data['by_user']
        user1_entry = next((u for u in by_user if u['user_id'] == self.user1.id), None)
        self.assertIsNotNone(user1_entry)
        self.assertEqual(user1_entry['name'], 'User One')
        self.assertEqual(user1_entry['calls'], 2)
        self.assertEqual(user1_entry['seconds'], 300)

        unknown_entry = next((u for u in by_user if u['user_id'] is None), None)
        self.assertIsNotNone(unknown_entry)
        self.assertEqual(unknown_entry['name'], 'Unknown')
        self.assertEqual(unknown_entry['calls'], 1)
        self.assertEqual(unknown_entry['seconds'], 60)

    def test_summary_top_10_callers_limit(self):
        from datetime import datetime, timezone as dt_tz
        from apps.lead.models import LeadCall
        # Create 11 distinct users each with 1 call
        for i in range(11):
            u = User.objects.create_user(email=f'caller{i}@test.com', name=f'Caller {i}', password='p')
            LeadCall.objects.create(
                lead=self.lead, call_timestamp=datetime(2026, 10, 20, 10, 0, 0, tzinfo=dt_tz.utc),
                direction='outbound', caller=u.name, phone_number='9840000000',
                duration_seconds=30, created_by=u, branch=self.branch,
            )
        response = self.client.get('/api/calls/summary/')
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(response.data['by_user']), 10)

    def test_summary_empty_dataset(self):
        response = self.client.get('/api/calls/summary/?search=NonExistentQueryXYZ')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total_calls'], 0)
        self.assertEqual(response.data['inbound'], 0)
        self.assertEqual(response.data['outbound'], 0)
        self.assertEqual(response.data['total_seconds'], 0)
        self.assertEqual(response.data['total_formatted'], '00:00:00')
        self.assertEqual(response.data['avg_seconds'], 0)
        self.assertEqual(response.data['connected_calls'], 0)
        self.assertEqual(response.data['connect_rate'], 0.0)
        self.assertEqual(response.data['by_day'], [])
        self.assertEqual(response.data['by_user'], [])

    def test_summary_filter_preservation(self):
        response = self.client.get('/api/calls/summary/?direction=inbound')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total_calls'], 2)
        self.assertEqual(response.data['inbound'], 2)
        self.assertEqual(response.data['outbound'], 0)


class GlobalCallExportTests(APITestCase):
    def setUp(self):
        from datetime import datetime, timezone as dt_tz
        from apps.branch.models import Branch
        from apps.lead.models import LeadCall

        self.branch = Branch.objects.create(name='Lalitpur', code='LLT')
        self.admin = User.objects.create_user(
            email='admin@exporttest.com', name='Export Admin', password='p', is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.lead = Lead.objects.create(
            full_name='Nepal Lead', phone='9841234567', branch=self.branch,
        )
        self.call = LeadCall.objects.create(
            lead=self.lead,
            call_timestamp=datetime(2026, 10, 25, 8, 30, 0, tzinfo=dt_tz.utc),
            direction='outbound',
            caller='Export Admin',
            phone_number='9841234567',
            duration_seconds=259,
            outcome='Interested',
            notes='=HYPERLINK("http://evil.com", "Malicious Link")',
            recording_url='https://rec.example.com/audio1.mp3',
            created_by=self.admin,
            branch=self.branch,
        )
        self.formula_call = LeadCall.objects.create(
            lead=self.lead,
            call_timestamp=datetime(2026, 10, 25, 9, 0, 0, tzinfo=dt_tz.utc),
            direction='inbound',
            caller='+9779841234567',
            phone_number='-9841234567',
            duration_seconds=60,
            outcome='@DangerousOutcome',
            notes='+cmd|/C calc!A0',
            created_by=self.admin,
            branch=self.branch,
        )

    def test_export_content_type_and_utf8_bom(self):
        import codecs
        response = self.client.get('/api/calls/export/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Content-Type'], 'text/csv; charset=utf-8')
        self.assertTrue('attachment; filename="calls-' in response.headers['Content-Disposition'])
        content = b"".join(response.streaming_content)
        self.assertTrue(content.startswith(codecs.BOM_UTF8))

    def test_export_csv_headers_and_row_count(self):
        import csv
        import io
        response = self.client.get('/api/calls/export/')
        self.assertEqual(response.status_code, 200)
        # Strip UTF-8 BOM
        text = b"".join(response.streaming_content).decode('utf-8-sig')
        reader = list(csv.reader(io.StringIO(text)))
        self.assertGreaterEqual(len(reader), 3)  # header + 2 rows
        headers = reader[0]
        expected_headers = [
            'Date & time (NPT)',
            'Direction',
            'Lead',
            'Reference',
            'Phone',
            'Caller',
            'Logged by',
            'Duration (MM:SS)',
            'Seconds',
            'Outcome',
            'Notes',
            'Recording',
        ]
        self.assertEqual(headers, expected_headers)

    def test_export_formula_neutralization(self):
        import csv
        import io
        response = self.client.get('/api/calls/export/')
        self.assertEqual(response.status_code, 200)
        text = b"".join(response.streaming_content).decode('utf-8-sig')
        reader = list(csv.reader(io.StringIO(text)))
        rows = reader[1:]

        # Find row for self.call
        row1 = next((r for r in rows if 'audio1.mp3' in r[11]), None)
        self.assertIsNotNone(row1)
        # Note should start with '=HYPERLINK... -> neutralized with prefix '
        self.assertTrue(row1[10].startswith("'=HYPERLINK"))
        self.assertEqual(row1[7], '04:19')  # 259 seconds formatted as MM:SS
        self.assertEqual(row1[8], '259')

        # Find row for self.formula_call
        row2 = next((r for r in rows if r[1] == 'inbound'), None)
        self.assertIsNotNone(row2)
        # Caller '+977...' -> neutralized
        self.assertTrue(row2[5].startswith("'+977"))
        # Phone '-984...' -> neutralized
        self.assertTrue(row2[4].startswith("'-984"))
        # Outcome '@DangerousOutcome' -> neutralized
        self.assertTrue(row2[9].startswith("'@DangerousOutcome"))
        # Notes '+cmd...' -> neutralized
        self.assertTrue(row2[10].startswith("'+cmd"))

    def test_export_filter_preservation(self):
        import csv
        import io
        response = self.client.get('/api/calls/export/?direction=inbound')
        self.assertEqual(response.status_code, 200)
        text = b"".join(response.streaming_content).decode('utf-8-sig')
        reader = list(csv.reader(io.StringIO(text)))
        # header + 1 matching row
        self.assertEqual(len(reader), 2)
        self.assertEqual(reader[1][1], 'inbound')


class GlobalCallSecurityTests(APITestCase):
    def setUp(self):
        from datetime import datetime, timezone as dt_tz
        from apps.branch.models import Branch
        from apps.lead.models import LeadCall

        self.branch_a = Branch.objects.create(name='Branch A', code='BRA')
        self.branch_b = Branch.objects.create(name='Branch B', code='BRB')

        self.user_a = User.objects.create_user(
            email='staff_a@test.com', name='Staff A', password='p',
            is_staff=False, branch=self.branch_a,
        )
        self.user_b = User.objects.create_user(
            email='staff_b@test.com', name='Staff B', password='p',
            is_staff=False, branch=self.branch_b,
        )
        self.admin = User.objects.create_user(
            email='admin@test.com', name='Global Admin', password='p',
            is_staff=True,
        )

        lead_a = Lead.objects.create(full_name='Lead A', phone='9810000001', branch=self.branch_a)
        lead_b = Lead.objects.create(full_name='Lead B', phone='9810000002', branch=self.branch_b)

        self.call_a = LeadCall.objects.create(
            lead=lead_a, call_timestamp=datetime(2026, 10, 20, 10, 0, 0, tzinfo=dt_tz.utc),
            direction='outbound', caller='Staff A', phone_number='9810000001',
            duration_seconds=100, outcome='Interested', created_by=self.user_a, branch=self.branch_a,
        )
        self.call_b = LeadCall.objects.create(
            lead=lead_b, call_timestamp=datetime(2026, 10, 20, 11, 0, 0, tzinfo=dt_tz.utc),
            direction='inbound', caller='Staff B', phone_number='9810000002',
            duration_seconds=200, outcome='Interested', created_by=self.user_b, branch=self.branch_b,
        )

    def test_non_staff_branch_scoping_calls_list(self):
        # User in Branch A cannot see Branch B calls in list
        self.client.force_authenticate(self.user_a)
        response = self.client.get('/api/calls/')
        self.assertEqual(response.status_code, 200)
        call_ids = [r['id'] for r in response.data['results']]
        self.assertIn(self.call_a.id, call_ids)
        self.assertNotIn(self.call_b.id, call_ids)

    def test_non_staff_branch_scoping_calls_summary(self):
        # User in Branch A only sees summary for Branch A calls
        self.client.force_authenticate(self.user_a)
        response = self.client.get('/api/calls/summary/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total_calls'], 1)
        self.assertEqual(response.data['total_seconds'], 100)

    def test_non_staff_branch_scoping_calls_export(self):
        # User in Branch A only exports Branch A calls
        import csv
        import io
        self.client.force_authenticate(self.user_a)
        response = self.client.get('/api/calls/export/')
        self.assertEqual(response.status_code, 200)
        text = b"".join(response.streaming_content).decode('utf-8-sig')
        reader = list(csv.reader(io.StringIO(text)))
        # header + 1 row for call_a
        self.assertEqual(len(reader), 2)
        self.assertIn('9810000001', reader[1][4])

    def test_staff_sees_all_branches_and_can_filter(self):
        self.client.force_authenticate(self.admin)
        # All calls
        response_all = self.client.get('/api/calls/')
        self.assertEqual(response_all.data['count'], 2)

        # Filter by branch_id
        response_bra = self.client.get(f'/api/calls/?branch_id={self.branch_a.id}')
        self.assertEqual(response_bra.data['count'], 1)
        self.assertEqual(response_bra.data['results'][0]['id'], self.call_a.id)

        # Summary filtered by branch_id
        sum_bra = self.client.get(f'/api/calls/summary/?branch_id={self.branch_a.id}')
        self.assertEqual(sum_bra.data['total_calls'], 1)
        self.assertEqual(sum_bra.data['total_seconds'], 100)

    def test_non_staff_cannot_override_assigned_branch(self):
        import csv
        import io

        self.client.force_authenticate(self.user_a)

        # 1. Calls list with ?branch_id=<Branch B ID>
        res_list = self.client.get(f'/api/calls/?branch_id={self.branch_b.id}')
        self.assertEqual(res_list.status_code, 200)
        call_ids = [r['id'] for r in res_list.data['results']]
        self.assertIn(self.call_a.id, call_ids)
        self.assertNotIn(self.call_b.id, call_ids)
        self.assertEqual(res_list.data['count'], 1)
        phones = [r['phone_number'] for r in res_list.data['results']]
        self.assertNotIn(self.call_b.phone_number, phones)
        lead_names = [r['lead_name'] for r in res_list.data['results']]
        self.assertNotIn(self.call_b.lead.full_name, lead_names)

        # 2. Calls summary with ?branch_id=<Branch B ID>
        res_summary = self.client.get(f'/api/calls/summary/?branch_id={self.branch_b.id}')
        self.assertEqual(res_summary.status_code, 200)
        self.assertEqual(res_summary.data['total_calls'], 1)
        self.assertEqual(res_summary.data['total_seconds'], 100)
        user_ids = [u['user_id'] for u in res_summary.data['by_user']]
        self.assertIn(self.user_a.id, user_ids)
        self.assertNotIn(self.user_b.id, user_ids)

        # 3. Calls export with ?branch_id=<Branch B ID>
        res_export = self.client.get(f'/api/calls/export/?branch_id={self.branch_b.id}')
        self.assertEqual(res_export.status_code, 200)
        text = b"".join(res_export.streaming_content).decode('utf-8-sig')
        reader = list(csv.reader(io.StringIO(text)))
        self.assertEqual(len(reader), 2)
        self.assertIn(self.call_a.phone_number, reader[1][4])
        self.assertIn(self.call_a.lead.full_name, reader[1][2])
        self.assertNotIn(self.call_b.phone_number, text)
        self.assertNotIn(self.call_b.lead.full_name, text)

    def test_staff_can_filter_export_by_branch(self):
        import csv
        import io

        self.client.force_authenticate(self.admin)

        # 1. Export all branches when no branch filter is supplied
        res_all = self.client.get('/api/calls/export/')
        self.assertEqual(res_all.status_code, 200)
        text_all = b"".join(res_all.streaming_content).decode('utf-8-sig')
        reader_all = list(csv.reader(io.StringIO(text_all)))
        self.assertEqual(len(reader_all), 3)
        self.assertIn(self.call_a.phone_number, text_all)
        self.assertIn(self.call_b.phone_number, text_all)

        # 2. Export filtered by branch_id=<Branch A ID>
        res_bra = self.client.get(f'/api/calls/export/?branch_id={self.branch_a.id}')
        self.assertEqual(res_bra.status_code, 200)
        text_bra = b"".join(res_bra.streaming_content).decode('utf-8-sig')
        reader_bra = list(csv.reader(io.StringIO(text_bra)))

        expected_headers = [
            'Date & time (NPT)',
            'Direction',
            'Lead',
            'Reference',
            'Phone',
            'Caller',
            'Logged by',
            'Duration (MM:SS)',
            'Seconds',
            'Outcome',
            'Notes',
            'Recording',
        ]
        self.assertEqual(reader_bra[0], expected_headers)
        self.assertEqual(len(reader_bra), 2)
        self.assertIn(self.call_a.phone_number, reader_bra[1][4])
        self.assertIn(self.call_a.lead.full_name, reader_bra[1][2])
        self.assertNotIn(self.call_b.phone_number, text_bra)
        self.assertNotIn(self.call_b.lead.full_name, text_bra)
