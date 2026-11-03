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

    def test_summary_returns_correct_totals(self):
        response = self.client.get('/api/calls/summary/')

        self.assertEqual(response.status_code, 200)

        totals = response.data['totals']

        self.assertEqual(totals['total_calls'], 2)
        self.assertEqual(totals['inbound_calls'], 1)
        self.assertEqual(totals['outbound_calls'], 1)
        self.assertEqual(totals['total_talk_time_seconds'], 330)
        self.assertEqual(totals['average_duration_seconds'], 165)
        self.assertEqual(totals['connected_calls'], 2)
        self.assertEqual(totals['connect_rate'], 100)

    def test_summary_respects_direction_filter(self):
        response = self.client.get(
            '/api/calls/summary/?direction=inbound'
        )

        self.assertEqual(response.status_code, 200)

        totals = response.data['totals']

        self.assertEqual(totals['total_calls'], 1)
        self.assertEqual(totals['inbound_calls'], 1)
        self.assertEqual(totals['outbound_calls'], 0)
        self.assertEqual(totals['total_talk_time_seconds'], 60)

    def test_summary_returns_top_callers(self):
        response = self.client.get('/api/calls/summary/')

        self.assertEqual(response.status_code, 200)

        callers = response.data['top_callers']

        self.assertEqual(len(callers), 2)
        self.assertEqual(callers[0]['caller_display'], 'Counselor Nine')
        self.assertEqual(callers[0]['total_calls'], 1)

    def test_summary_respects_branch_isolation_for_non_staff(self):
        from apps.branch.models import Branch
        from apps.lead.models import LeadCall

        other_branch = Branch.objects.create(
            name='Dharan',
            code='DHR',
        )

        non_staff_user = User.objects.create_user(
            email='nonstaff@test.com',
            name='Non Staff',
            password='pass',
            branch=self.branch,
        )

        LeadCall.objects.create(
            lead=self.lead,
            call_timestamp=self.recent_call.call_timestamp,
            direction='outbound',
            caller='Other Branch Caller',
            phone_number='9800000000',
            duration_seconds=120,
            created_by=non_staff_user,
            branch=other_branch,
        )

        self.client.force_authenticate(non_staff_user)

        response = self.client.get('/api/calls/summary/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['totals']['total_calls'], 2)

    def test_summary_groups_calls_by_nepal_date(self):
        from datetime import datetime, timezone as dt_timezone
        from apps.lead.models import LeadCall
        from datetime import date

        first_call_time = datetime(
            2025, 11, 1, 19, 0,
            tzinfo=dt_timezone.utc,
        )

        second_call_time = datetime(
            2025, 11, 2, 1, 0,
            tzinfo=dt_timezone.utc,
        )

        LeadCall.objects.create(
            lead=self.lead,
            call_timestamp=first_call_time,
            direction='outbound',
            caller='Test Caller',
            phone_number='9800000000',
            duration_seconds=60,
            created_by=self.admin,
            branch=self.branch,
        )

        LeadCall.objects.create(
            lead=self.lead,
            call_timestamp=second_call_time,
            direction='outbound',
            caller='Test Caller',
            phone_number='9800000000',
            duration_seconds=120,
            created_by=self.admin,
            branch=self.branch,
        )

        response = self.client.get('/api/calls/summary/')

        self.assertEqual(response.status_code, 200)

        november_second = next(
            (
                item for item in response.data['calls_per_day']
                if item['day'] == date(2025, 11, 2)
            ),
            None,
        )

        self.assertIsNotNone(november_second)
        self.assertEqual(november_second['total_calls'], 2)

    def test_summary_handles_empty_results(self):
        from apps.lead.models import LeadCall

        # Remove all calls from this test's database.
        LeadCall.objects.all().delete()

        # Request the summary API.
        response = self.client.get('/api/calls/summary/')

        self.assertEqual(response.status_code, 200)

        totals = response.data['totals']

        self.assertEqual(totals['total_calls'], 0)
        self.assertEqual(totals['total_talk_time_seconds'], 0)
        self.assertEqual(totals['average_duration_seconds'], 0)
        self.assertEqual(totals['connect_rate'], 0)

        self.assertEqual(response.data['calls_per_day'], [])
        self.assertEqual(response.data['top_callers'], [])

    def test_call_serializer_returns_formatted_duration(self):
        response = self.client.get('/api/calls/')

        self.assertEqual(response.status_code, 200)

        row = next(
            item for item in response.data['results']
            if item['id'] == self.recent_call.id
        )

        self.assertEqual(row['duration_seconds'], 270)
        self.assertEqual(row['duration_formatted'], '04:30')

    def test_export_returns_csv_file(self):
        response = self.client.get('/api/calls/export/')

        self.assertEqual(response.status_code, 200)

        self.assertEqual(
            response['Content-Type'],
            'text/csv; charset=utf-8',
        )

        self.assertIn(
            'attachment; filename="calls.csv"',
            response['Content-Disposition'],
        )

        content = b''.join(response.streaming_content).decode('utf-8')

        self.assertTrue(content.startswith('\ufeff'))
        self.assertIn('Lead Reference', content)
        self.assertIn('Counselor Nine', content)

    def test_export_respects_direction_filter(self):
        response = self.client.get(
            '/api/calls/export/?direction=inbound'
        )

        self.assertEqual(response.status_code, 200)

        content = b''.join(response.streaming_content).decode('utf-8')

        self.assertIn('Someone Else', content)
        self.assertNotIn('Counselor Nine', content)

    def test_export_neutralizes_spreadsheet_formulas(self):
        from apps.lead.models import LeadCall

        LeadCall.objects.create(
            lead=self.lead,
            call_timestamp=self.recent_call.call_timestamp,
            direction='outbound',
            caller='=SUM(1+1)',
            phone_number='+123456789',
            duration_seconds=60,
            created_by=self.admin,
            branch=self.branch,
        )

        response = self.client.get('/api/calls/export/')

        self.assertEqual(response.status_code, 200)

        content = b''.join(response.streaming_content).decode('utf-8')

        self.assertIn("'=SUM(1+1)", content)
        self.assertIn("'+123456789", content)

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

    def test_staff_branch_filter_applies_to_list_summary_and_export(self):
        from apps.branch.models import Branch
        from apps.lead.models import LeadCall

        other_branch = Branch.objects.create(name='Dharan', code='DHR')
        other_call = LeadCall.objects.create(
            lead=self.lead,
            call_timestamp=self.recent_call.call_timestamp,
            direction='inbound',
            caller='Dharan Caller',
            phone_number='9800000000',
            duration_seconds=90,
            created_by=self.admin,
            branch=other_branch,
        )

        response = self.client.get(f'/api/calls/?branch_id={other_branch.id}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row['id'] for row in response.data['results']], [other_call.id])

        summary = self.client.get(f'/api/calls/summary/?branch_id={other_branch.id}')
        self.assertEqual(summary.data['totals']['total_calls'], 1)

        exported = self.client.get(f'/api/calls/export/?branch_id={other_branch.id}')
        content = b''.join(exported.streaming_content).decode('utf-8')
        self.assertIn('Dharan Caller', content)
        self.assertNotIn('Counselor Nine', content)

    def test_non_staff_cannot_override_assigned_branch_with_query_filter(self):
        from apps.branch.models import Branch
        from apps.lead.models import LeadCall

        other_branch = Branch.objects.create(name='Dharan', code='DHR')
        other_call = LeadCall.objects.create(
            lead=self.lead,
            call_timestamp=self.recent_call.call_timestamp,
            direction='outbound',
            caller='Other Branch Caller',
            phone_number='9800000000',
            duration_seconds=90,
            created_by=self.admin,
            branch=other_branch,
        )
        user = User.objects.create_user(
            email='branch-user@test.com', name='Branch User', password='pass',
            branch=self.branch,
        )
        self.client.force_authenticate(user)

        response = self.client.get(f'/api/calls/?branch_id={other_branch.id}')
        ids = [row['id'] for row in response.data['results']]
        self.assertCountEqual(ids, [self.recent_call.id, self.old_call.id])
        self.assertNotIn(other_call.id, ids)

    def test_unassigned_non_staff_branch_behavior_is_unscoped(self):
        from apps.branch.models import Branch
        from apps.lead.models import LeadCall

        other_branch = Branch.objects.create(name='Dharan', code='DHR')
        LeadCall.objects.create(
            lead=self.lead,
            call_timestamp=self.recent_call.call_timestamp,
            direction='outbound',
            caller='Other Branch Caller',
            phone_number='9800000000',
            duration_seconds=90,
            created_by=self.admin,
            branch=other_branch,
        )
        unassigned = User.objects.create_user(
            email='unassigned@test.com', name='Unassigned', password='pass',
        )
        self.client.force_authenticate(unassigned)

        response = self.client.get('/api/calls/')
        self.assertEqual(response.data['count'], 3)

    def test_summary_respects_search_and_creator_filters(self):
        response = self.client.get(
            f'/api/calls/summary/?search=Call%20Person&created_by={self.admin.id}'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['totals']['total_calls'], 1)

    def test_export_includes_all_matching_records_not_current_page(self):
        import csv
        import io
        from apps.lead.models import LeadCall

        for index in range(21):
            LeadCall.objects.create(
                lead=self.lead,
                call_timestamp=self.recent_call.call_timestamp,
                direction='outbound',
                caller=f'Export Caller {index}',
                phone_number='9800000000',
                duration_seconds=60,
                created_by=self.admin,
                branch=self.branch,
            )

        response = self.client.get('/api/calls/export/?page=2&page_size=1')
        content = b''.join(response.streaming_content).decode('utf-8')
        rows = list(csv.reader(io.StringIO(content)))
        self.assertEqual(len(rows), 24)
