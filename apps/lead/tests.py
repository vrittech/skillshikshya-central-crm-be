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
        self.assertEqual(row['duration_formatted'], '04:30')

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

    def test_export_csv_has_required_columns_npt_and_all_filtered_rows(self):
        import csv
        import io
        from apps.lead.models import LeadCall

        formula_lead = Lead.objects.create(
            full_name='सीता Export Test', phone='9844444444',
        )
        LeadCall.objects.create(
            lead=formula_lead, call_timestamp=self.recent_call.call_timestamp,
            direction='outbound', caller='Counselor Nine',
            phone_number='9833333333', duration_seconds=259,
            outcome='interested', notes='=HYPERLINK("https://example.com")',
            recording_url='https://example.com/recording',
            created_by=self.counselor, branch=self.branch,
        )

        response = self.client.get('/api/calls/export/?direction=outbound&page_size=1')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        self.assertRegex(
            response['Content-Disposition'],
            r'attachment; filename="calls-\d{4}-\d{2}-\d{2}\.csv"',
        )
        self.assertTrue(response.streaming)
        csv_bytes = b''.join(response.streaming_content)
        self.assertTrue(csv_bytes.startswith(b'\xef\xbb\xbf'))
        csv_content = csv_bytes.decode('utf-8-sig')
        rows = list(csv.reader(io.StringIO(csv_content)))
        self.assertEqual(rows[0], [
            'Date & time (NPT)', 'Direction', 'Lead', 'Reference', 'Phone',
            'Caller', 'Logged by', 'Duration (MM:SS)', 'Seconds', 'Outcome',
            'Notes', 'Recording',
        ])
        self.assertEqual(len(rows), 3)
        self.assertEqual({row[2] for row in rows[1:]}, {
            self.lead.full_name, formula_lead.full_name,
        })
        exported_formula_call = next(
            row for row in rows[1:] if row[2] == formula_lead.full_name
        )
        self.assertEqual(exported_formula_call[0][-6:], '+05:45')
        self.assertEqual(exported_formula_call[1], 'outbound')
        self.assertEqual(exported_formula_call[5], 'Counselor Nine')
        self.assertEqual(exported_formula_call[6], 'Counselor Nine')
        self.assertEqual(exported_formula_call[7:10], ['04:19', '259', 'interested'])
        self.assertEqual(
            exported_formula_call[10], '\'=HYPERLINK("https://example.com")',
        )
        self.assertEqual(
            exported_formula_call[11], 'https://example.com/recording',
        )

    def test_summary_aggregates_totals_and_includes_unknown_creator(self):
        from apps.lead.models import LeadCall

        LeadCall.objects.create(
            lead=self.lead, call_timestamp=self.recent_call.call_timestamp,
            direction='outbound', caller='Unknown Caller',
            phone_number='9833333333', duration_seconds=0,
            created_by=None, branch=self.branch,
        )

        response = self.client.get('/api/calls/summary/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['total_calls'], 3)
        self.assertEqual(response.data['inbound'], 1)
        self.assertEqual(response.data['outbound'], 2)
        self.assertEqual(response.data['total_seconds'], 330)
        self.assertEqual(response.data['total_formatted'], '00:05:30')
        self.assertEqual(response.data['avg_seconds'], 110)
        self.assertEqual(response.data['connected_calls'], 2)
        self.assertEqual(response.data['connect_rate'], 0.667)
        unknown_user = next(
            user for user in response.data['by_user'] if user['user_id'] is None
        )
        self.assertEqual(unknown_user['name'], 'Unknown')
        self.assertEqual(unknown_user['calls'], 1)
        self.assertEqual(unknown_user['seconds'], 0)

    def test_summary_uses_nepal_time_and_respects_filters(self):
        from datetime import datetime, timezone as datetime_timezone
        from apps.lead.models import LeadCall

        LeadCall.objects.create(
            lead=self.lead,
            call_timestamp=datetime(2026, 10, 28, 20, tzinfo=datetime_timezone.utc),
            direction='inbound', caller='Nepal Time Caller',
            phone_number='9844444444', duration_seconds=90,
            created_by=self.counselor, branch=self.branch,
        )

        response = self.client.get('/api/calls/summary/?direction=inbound')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['total_calls'], 2)
        self.assertEqual(response.data['inbound'], 2)
        self.assertIn(
            {'date': '2026-10-29', 'calls': 1, 'seconds': 90},
            response.data['by_day'],
        )
        self.assertEqual(
            next(
                row for row in response.data['by_user']
                if row['user_id'] == self.counselor.id
            )['calls'],
            1,
        )

    def test_summary_returns_zeros_for_no_matching_calls(self):
        response = self.client.get('/api/calls/summary/?search=no-such-call')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['total_calls'], 0)
        self.assertEqual(response.data['inbound'], 0)
        self.assertEqual(response.data['outbound'], 0)
        self.assertEqual(response.data['total_seconds'], 0)
        self.assertEqual(response.data['total_formatted'], '00:00:00')
        self.assertEqual(response.data['avg_seconds'], 0)
        self.assertEqual(response.data['connected_calls'], 0)
        self.assertEqual(response.data['connect_rate'], 0)
        self.assertEqual(response.data['by_day'], [])
        self.assertEqual(response.data['by_user'], [])

    def test_branch_scoping_for_non_staff(self):
        from apps.branch.models import Branch
        from apps.lead.models import LeadCall
        other_branch = Branch.objects.create(name='Dharan', code='DHR')
        other_lead = Lead.objects.create(
            full_name='Other Branch Lead', phone='9800000999',
        )
        other_call = LeadCall.objects.create(
            lead=other_lead, call_timestamp=self.recent_call.call_timestamp,
            direction='outbound', caller='Other Branch Counselor',
            phone_number='9800000999', duration_seconds=45,
            created_by=self.counselor, branch=other_branch,
        )
        branch_user = User.objects.create_user(
            email='branch-user@test.com', name='Branch User', password='x',
            branch=self.branch,
        )
        self.client.force_authenticate(branch_user)

        list_response = self.client.get('/api/calls/')
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        listed_ids = [row['id'] for row in list_response.data['results']]
        self.assertIn(self.recent_call.id, listed_ids)
        self.assertIn(self.old_call.id, listed_ids)
        self.assertNotIn(other_call.id, listed_ids)
        self.assertNotIn('Other Branch Lead', str(list_response.data))

        summary_response = self.client.get('/api/calls/summary/')
        self.assertEqual(summary_response.status_code, status.HTTP_200_OK)
        self.assertEqual(summary_response.data['total_calls'], 2)

        export_response = self.client.get('/api/calls/export/')
        self.assertEqual(export_response.status_code, status.HTTP_200_OK)
        export_content = b''.join(export_response.streaming_content).decode('utf-8-sig')
        self.assertIn('Call Person', export_content)
        self.assertNotIn('Other Branch Lead', export_content)

    def test_staff_branch_filter_applies_to_list_summary_and_export(self):
        import csv
        import io
        from apps.branch.models import Branch
        from apps.lead.models import LeadCall

        other_branch = Branch.objects.create(name='Dharan', code='DHR')
        other_lead = Lead.objects.create(
            full_name='Other Branch Lead', phone='9800000888',
        )
        LeadCall.objects.create(
            lead=other_lead, call_timestamp=self.recent_call.call_timestamp,
            direction='outbound', caller='Other Branch Counselor',
            phone_number='9800000888', duration_seconds=45,
            created_by=self.counselor, branch=other_branch,
        )
        self.client.force_authenticate(self.admin)

        all_calls = self.client.get('/api/calls/')
        self.assertEqual(all_calls.data['count'], 3)

        list_response = self.client.get(f'/api/calls/?branch_id={self.branch.id}')
        self.assertEqual(list_response.data['count'], 2)
        self.assertNotIn('Other Branch Lead', str(list_response.data))

        summary_response = self.client.get(
            f'/api/calls/summary/?branch_id={self.branch.id}',
        )
        self.assertEqual(summary_response.data['total_calls'], 2)

        export_response = self.client.get(
            f'/api/calls/export/?branch_id={self.branch.id}',
        )
        export_content = b''.join(export_response.streaming_content).decode('utf-8-sig')
        rows = list(csv.reader(io.StringIO(export_content)))
        self.assertEqual(len(rows), 3)
        self.assertNotIn('Other Branch Lead', export_content)

    def test_list_summary_and_export_use_a_constant_number_of_queries(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from apps.lead.models import LeadCall

        self.client.force_authenticate(self.admin)

        def query_counts():
            counts = {}
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get('/api/calls/')
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            counts['list'] = len(queries)

            with CaptureQueriesContext(connection) as queries:
                response = self.client.get('/api/calls/summary/')
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            counts['summary'] = len(queries)

            with CaptureQueriesContext(connection) as queries:
                response = self.client.get('/api/calls/export/')
                self.assertTrue(response.streaming)
                b''.join(response.streaming_content)
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            counts['export'] = len(queries)
            return counts

        counts_for_two_calls = query_counts()
        for index in range(25):
            LeadCall.objects.create(
                lead=self.lead,
                call_timestamp=self.recent_call.call_timestamp,
                direction='outbound',
                caller=f'Counselor {index}',
                phone_number='9811111111',
                duration_seconds=index,
                created_by=self.counselor,
                branch=self.branch,
            )
        counts_for_many_calls = query_counts()

        self.assertEqual(counts_for_two_calls, counts_for_many_calls)
