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

    def test_summary_totals_connect_rate_and_nepal_day(self):
        from datetime import datetime, timezone
        from apps.lead.models import LeadCall

        nepali_day_utc = datetime(2026, 10, 28, 20, 0, tzinfo=timezone.utc)
        self.recent_call.call_timestamp = nepali_day_utc
        self.recent_call.save(update_fields=['call_timestamp'])
        self.old_call.call_timestamp = nepali_day_utc
        self.old_call.save(update_fields=['call_timestamp'])

        zero_call = LeadCall.objects.create(
            lead=self.lead,
            call_timestamp=nepali_day_utc,
            direction='outbound',
            caller='Unknown Caller',
            phone_number='9833333333',
            duration_seconds=0,
            created_by=None,
            branch=self.branch,
        )

        response = self.client.get('/api/calls/summary/')
        self.assertEqual(response.status_code, 200)

        data = response.data
        self.assertEqual(data['total_calls'], 3)
        self.assertEqual(data['inbound'], 1)
        self.assertEqual(data['outbound'], 2)
        self.assertEqual(data['total_seconds'], 330)
        self.assertEqual(data['total_formatted'], '00:05:30')
        self.assertEqual(data['avg_seconds'], 110)
        self.assertEqual(data['connected_calls'], 2)
        self.assertEqual(data['connect_rate'], 0.667)

        self.assertEqual(data['by_day'], [{
            'date': '2026-10-29',
            'calls': 3,
            'seconds': 330,
        }])

        unknown_caller = next(
            row for row in data['by_user']
            if row['user_id'] is None
        )
        self.assertEqual(unknown_caller['name'], 'Unknown')
        self.assertEqual(unknown_caller['calls'], 1)
        self.assertEqual(zero_call.duration_seconds, 0)

    def test_summary_returns_zeros_when_no_calls_match(self):
        response = self.client.get('/api/calls/summary/?created_by=999999')
        self.assertEqual(response.status_code, 200)

        data = response.data
        self.assertEqual(data['total_calls'], 0)
        self.assertEqual(data['inbound'], 0)
        self.assertEqual(data['outbound'], 0)
        self.assertEqual(data['total_seconds'], 0)
        self.assertEqual(data['total_formatted'], '00:00:00')
        self.assertEqual(data['avg_seconds'], 0)
        self.assertEqual(data['connected_calls'], 0)
        self.assertEqual(data['connect_rate'], 0)
        self.assertEqual(data['by_day'], [])
        self.assertEqual(data['by_user'], [])

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

    def test_export_csv_headers_and_rows(self):
        import csv
        import io

        response = self.client.get('/api/calls/export/')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Type'].startswith('text/csv'))
        self.assertIn('attachment; filename="calls-', response['Content-Disposition'])
        self.assertTrue(response.content.startswith(b'\xef\xbb\xbf'))

        text = response.content.decode('utf-8-sig')
        rows = list(csv.reader(io.StringIO(text)))

        self.assertEqual(rows[0], [
            'Date & time (NPT)', 'Direction', 'Lead', 'Reference', 'Phone',
            'Caller', 'Logged by', 'Duration (MM:SS)', 'Seconds', 'Outcome',
            'Notes', 'Recording',
        ])
        self.assertEqual(len(rows), 3)  # One header row and two call rows.

    def test_export_csv_respects_filters(self):
        import csv
        import io

        response = self.client.get('/api/calls/export/?direction=inbound')

        self.assertEqual(response.status_code, 200)
        text = response.content.decode('utf-8-sig')
        rows = list(csv.reader(io.StringIO(text)))

        self.assertEqual(len(rows), 2)  # Header plus the one inbound call.
        self.assertEqual(rows[1][1], 'Inbound')

    def test_export_csv_neutralizes_formula_notes(self):
        import csv
        import io

        self.recent_call.notes = '=HYPERLINK("https://example.com","click")'
        self.recent_call.save(update_fields=['notes'])

        response = self.client.get(
            f'/api/calls/export/?created_by={self.counselor.id}'
        )

        self.assertEqual(response.status_code, 200)
        text = response.content.decode('utf-8-sig')
        rows = list(csv.reader(io.StringIO(text)))

        self.assertEqual(len(rows), 2)
        self.assertEqual(
            rows[1][10],
            '\'=HYPERLINK("https://example.com","click")',
        )

    def test_branch_scoping_for_non_staff(self):
        import csv
        import io
        from apps.branch.models import Branch
        from apps.lead.models import LeadCall

        other_branch = Branch.objects.create(name='Dharan', code='DHR')
        other_call = LeadCall.objects.create(
            lead=self.lead,
            call_timestamp=self.recent_call.call_timestamp,
            direction='outbound',
            caller='Other Branch Counselor',
            phone_number='9800000099',
            duration_seconds=45,
            created_by=self.counselor,
            branch=other_branch,
        )
        branch_user = User.objects.create_user(
            email='branch-user@test.com',
            name='Branch User',
            password='pass',
            branch=self.branch,
        )
        self.client.force_authenticate(branch_user)

        list_response = self.client.get('/api/calls/')
        self.assertEqual(list_response.status_code, 200)
        listed_ids = [row['id'] for row in list_response.data['results']]
        self.assertIn(self.recent_call.id, listed_ids)
        self.assertIn(self.old_call.id, listed_ids)
        self.assertNotIn(other_call.id, listed_ids)

        summary_response = self.client.get('/api/calls/summary/')
        self.assertEqual(summary_response.status_code, 200)
        self.assertEqual(summary_response.data['total_calls'], 2)
        self.assertEqual(summary_response.data['total_seconds'], 330)

        export_response = self.client.get('/api/calls/export/')
        self.assertEqual(export_response.status_code, 200)
        rows = list(csv.reader(
            io.StringIO(export_response.content.decode('utf-8-sig'))
        ))
        self.assertEqual(len(rows), 3)  # Header plus two calls.
        exported_phones = [row[4] for row in rows[1:]]
        self.assertNotIn(other_call.phone_number, exported_phones)

    def test_staff_can_see_all_branches_and_filter_to_one(self):
        import csv
        import io
        from apps.branch.models import Branch
        from apps.lead.models import LeadCall

        other_branch = Branch.objects.create(name='Dharan', code='DHR')
        LeadCall.objects.create(
            lead=self.lead,
            call_timestamp=self.recent_call.call_timestamp,
            direction='outbound',
            caller='Other Branch Counselor',
            phone_number='9800000099',
            duration_seconds=45,
            created_by=self.counselor,
            branch=other_branch,
        )

        all_list = self.client.get('/api/calls/')
        self.assertEqual(all_list.status_code, 200)
        self.assertEqual(all_list.data['count'], 3)

        all_summary = self.client.get('/api/calls/summary/')
        self.assertEqual(all_summary.status_code, 200)
        self.assertEqual(all_summary.data['total_calls'], 3)

        all_export = self.client.get('/api/calls/export/')
        all_rows = list(csv.reader(
            io.StringIO(all_export.content.decode('utf-8-sig'))
        ))
        self.assertEqual(len(all_rows), 4)  # Header plus three calls.

        branch_url = f'/api/calls/?branch_id={self.branch.id}'
        branch_list = self.client.get(branch_url)
        self.assertEqual(branch_list.status_code, 200)
        self.assertEqual(branch_list.data['count'], 2)

        branch_summary = self.client.get(
            f'/api/calls/summary/?branch_id={self.branch.id}'
        )
        self.assertEqual(branch_summary.status_code, 200)
        self.assertEqual(branch_summary.data['total_calls'], 2)

        branch_export = self.client.get(
            f'/api/calls/export/?branch_id={self.branch.id}'
        )
        branch_rows = list(csv.reader(
            io.StringIO(branch_export.content.decode('utf-8-sig'))
        ))
        self.assertEqual(len(branch_rows), 3)  # Header plus two calls.
        exported_phones = [row[4] for row in branch_rows[1:]]
        self.assertNotIn('9800000099', exported_phones)