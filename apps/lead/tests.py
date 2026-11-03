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


class GlobalCallBranchScopingTests(APITestCase):
    def setUp(self):
        from django.utils import timezone
        from apps.branch.models import Branch
        from apps.lead.models import LeadCall

        self.branch_x = Branch.objects.create(name='Biratnagar', code='BRT')
        self.branch_y = Branch.objects.create(name='Dharan', code='DHR')
        self.user_x = User.objects.create_user(
            email='counselor.x@test.com', name='Counselor X', password='pass', branch=self.branch_x,
        )
        self.user_y = User.objects.create_user(
            email='counselor.y@test.com', name='Counselor Y', password='pass', branch=self.branch_y,
        )
        self.staff = User.objects.create_user(
            email='staff.branches@test.com', name='Branch Staff', password='pass', is_staff=True,
        )
        self.lead_x = Lead.objects.create(
            full_name='Biratnagar Lead', phone='9800000001', branch=self.branch_x,
        )
        self.lead_y = Lead.objects.create(
            full_name='Dharan Lead', phone='9800000002', branch=self.branch_y,
        )
        timestamp = timezone.now()
        self.call_x = LeadCall.objects.create(
            lead=self.lead_x, call_timestamp=timestamp, caller='Counselor X',
            phone_number=self.lead_x.phone, direction='outbound', duration_seconds=30,
            branch=self.branch_x, created_by=self.user_x,
        )
        self.call_y = LeadCall.objects.create(
            lead=self.lead_y, call_timestamp=timestamp, caller='Counselor Y',
            phone_number=self.lead_y.phone, direction='inbound', duration_seconds=60,
            branch=self.branch_y, created_by=self.user_y,
        )

    def _export_rows(self, response):
        import csv
        from io import StringIO

        content = b''.join(response.streaming_content).decode('utf-8-sig')
        return list(csv.reader(StringIO(content)))

    def test_non_staff_list_is_limited_to_assigned_branch(self):
        self.client.force_authenticate(self.user_x)
        response = self.client.get(f'/api/calls/?branch_id={self.branch_y.id}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual([row['id'] for row in response.data['results']], [self.call_x.id])
        self.assertNotIn(self.lead_y.phone, str(response.data))
        self.assertNotIn(self.lead_y.full_name, str(response.data))

    def test_non_staff_summary_is_limited_to_assigned_branch(self):
        self.client.force_authenticate(self.user_x)
        response = self.client.get(f'/api/calls/summary/?branch_id={self.branch_y.id}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total_calls'], 1)
        self.assertEqual(response.data['inbound'], 0)
        self.assertEqual(response.data['outbound'], 1)
        self.assertEqual(sum(day['calls'] for day in response.data['by_day']), 1)
        self.assertEqual(sum(user['calls'] for user in response.data['by_user']), 1)
        self.assertEqual([user['name'] for user in response.data['by_user']], ['Counselor X'])

    def test_non_staff_export_is_limited_to_assigned_branch(self):
        self.client.force_authenticate(self.user_x)
        response = self.client.get(f'/api/calls/export/?branch_id={self.branch_y.id}')
        rows = self._export_rows(response)
        self.assertEqual(len(rows), 2)
        self.assertIn(self.lead_x.phone, rows[1])
        self.assertNotIn(self.lead_y.phone, str(rows))
        self.assertNotIn(self.lead_y.full_name, str(rows))

    def test_staff_sees_all_branches_and_can_filter_branch_id(self):
        self.client.force_authenticate(self.staff)
        listing = self.client.get('/api/calls/')
        self.assertEqual(listing.data['count'], 2)
        summary = self.client.get('/api/calls/summary/')
        self.assertEqual(summary.data['total_calls'], 2)
        exported = self._export_rows(self.client.get('/api/calls/export/'))
        self.assertEqual(len(exported), 3)
        self.assertIn(self.lead_y.phone, str(exported))

        branch_filter = f'?branch_id={self.branch_x.id}'
        filtered_list = self.client.get(f'/api/calls/{branch_filter}')
        self.assertEqual(filtered_list.data['count'], 1)
        self.assertEqual(filtered_list.data['results'][0]['id'], self.call_x.id)
        filtered_summary = self.client.get(f'/api/calls/summary/{branch_filter}')
        self.assertEqual(filtered_summary.data['total_calls'], 1)
        filtered_export = self._export_rows(self.client.get(f'/api/calls/export/{branch_filter}'))
        self.assertEqual(len(filtered_export), 2)
        self.assertIn(self.lead_x.phone, filtered_export[1])
        self.assertNotIn(self.lead_y.phone, str(filtered_export))


class GlobalCallSummaryTests(APITestCase):
    def setUp(self):
        from datetime import datetime, timezone
        from apps.lead.models import LeadCall

        self.admin = User.objects.create_user(
            email='summary@test.com', name='Summary Admin', password='pass', is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.lead = Lead.objects.create(full_name='Summary Lead', phone='9812345678')
        common = {
            'lead': self.lead, 'caller': 'Caller', 'phone_number': '9812345678',
        }
        LeadCall.objects.create(
            **common, call_timestamp=datetime(2026, 10, 28, 20, 0, tzinfo=timezone.utc),
            direction='inbound', duration_seconds=0,
        )
        LeadCall.objects.create(
            **common, call_timestamp=datetime(2026, 10, 28, 21, 0, tzinfo=timezone.utc),
            direction='outbound', duration_seconds=20,
        )
        LeadCall.objects.create(
            **common, call_timestamp=datetime(2026, 10, 29, 1, 0, tzinfo=timezone.utc),
            direction='outbound', duration_seconds=10,
        )

    def test_summary_totals_and_npt_day(self):
        response = self.client.get('/api/calls/summary/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total_calls'], 3)
        self.assertEqual((response.data['inbound'], response.data['outbound']), (1, 2))
        self.assertEqual(response.data['total_seconds'], 30)
        self.assertEqual(response.data['avg_seconds'], 10)
        self.assertEqual(response.data['connected_calls'], 2)
        self.assertAlmostEqual(response.data['connect_rate'], 2 / 3)
        oct_29 = next(day for day in response.data['by_day'] if day['date'] == '2026-10-29')
        self.assertEqual(oct_29['calls'], 3)
        self.assertEqual(response.data['by_user'][0]['name'], 'Unknown')

    def test_empty_summary_returns_zeros_and_empty_groups(self):
        response = self.client.get('/api/calls/summary/?search=not-a-call')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total_calls'], 0)
        self.assertEqual(response.data['total_seconds'], 0)
        self.assertEqual(response.data['avg_seconds'], 0)
        self.assertEqual(response.data['connect_rate'], 0)
        self.assertEqual(response.data['by_day'], [])
        self.assertEqual(response.data['by_user'], [])


class GlobalCallExportTests(APITestCase):
    def setUp(self):
        from datetime import datetime, timezone
        from apps.lead.models import LeadCall

        self.admin = User.objects.create_user(
            email='export@test.com', name='Export Admin', password='pass', is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.lead = Lead.objects.create(full_name='नेपाली नाम', phone='9800000000')
        self.inbound = LeadCall.objects.create(
            lead=self.lead, call_timestamp=datetime(2026, 10, 28, 20, 0, tzinfo=timezone.utc),
            caller='Counselor', phone_number='9800000000', direction='inbound',
            duration_seconds=259, notes='=HYPERLINK("https://example.com")',
            created_by=self.admin,
        )
        LeadCall.objects.create(
            lead=self.lead, call_timestamp=datetime(2026, 10, 28, 21, 0, tzinfo=timezone.utc),
            caller='Counselor', phone_number='9800000000', direction='outbound',
            duration_seconds=0, created_by=self.admin,
        )

    def _csv_rows(self, response):
        import csv
        from io import StringIO

        content = b''.join(response.streaming_content).decode('utf-8-sig')
        return list(csv.reader(StringIO(content)))

    def test_csv_content_header_row_count_and_formula_neutralization(self):
        response = self.client.get('/api/calls/export/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Type'].startswith('text/csv'))
        rows = self._csv_rows(response)
        self.assertEqual(rows[0], [
            'Date & time (NPT)', 'Direction', 'Lead', 'Reference', 'Phone',
            'Caller', 'Logged by', 'Duration (MM:SS)', 'Seconds', 'Outcome',
            'Notes', 'Recording',
        ])
        self.assertEqual(len(rows), 3)
        formula_row = next(row for row in rows[1:] if row[10].startswith("'=HYPERLINK("))
        self.assertEqual(formula_row[0], '2026-10-29 01:45:00')
        self.assertEqual(formula_row[2], 'नेपाली नाम')
        self.assertEqual(formula_row[7:9], ['04:19', '259'])
        self.assertEqual(formula_row[10], "'=HYPERLINK(\"https://example.com\")")

    def test_export_respects_list_filters(self):
        response = self.client.get('/api/calls/export/?direction=inbound')
        rows = self._csv_rows(response)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][1], 'inbound')
