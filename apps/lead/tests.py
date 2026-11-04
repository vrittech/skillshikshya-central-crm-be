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

    def test_calls_summary_calculations(self):
        response = self.client.get('/api/calls/summary/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['total_calls'], 2)
        self.assertEqual(response.data['inbound_calls'], 1)
        self.assertEqual(response.data['outbound_calls'], 1)
        self.assertEqual(response.data['talk_time_seconds'], 330)
        self.assertEqual(response.data['average_duration_seconds'], 165)
        self.assertEqual(response.data['connected_calls'], 2)
        self.assertEqual(response.data['connect_rate'], 100)

    def test_calls_summary_respects_filters(self):
        response = self.client.get(
            '/api/calls/summary/?direction=outbound'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['total_calls'], 1)
        self.assertEqual(response.data['outbound_calls'], 1)
        self.assertEqual(response.data['inbound_calls'], 0)
        self.assertEqual(response.data['talk_time_seconds'], 270)

    def test_calls_csv_export(self):
        import csv
        from io import StringIO

        response = self.client.get('/api/calls/export_csv/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('text/csv', response['Content-Type'])
        self.assertIn('attachment;', response['Content-Disposition'])

        content = response.content.decode('utf-8-sig')
        rows = list(csv.reader(StringIO(content)))

        self.assertEqual(rows[0], [
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
        ])

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1][1], 'Outbound')
        self.assertEqual(rows[1][2], 'Call Person')
        self.assertEqual(rows[1][8], '270')

    def test_csv_export_prevents_formula_injection(self):
        import csv
        from io import StringIO

        self.recent_call.caller = '=1+1'
        self.recent_call.phone_number = '+123456789'
        self.recent_call.outcome = '-danger'
        self.recent_call.notes = '@formula'
        self.recent_call.save()

        response = self.client.get('/api/calls/export_csv/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        content = response.content.decode('utf-8-sig')
        rows = list(csv.reader(StringIO(content)))

        # Find the exported row for the modified call.
        call_row = next(row for row in rows if row[8] == '270')

        self.assertEqual(call_row[5], "'=1+1")
        self.assertEqual(call_row[4], "'+123456789")
        self.assertEqual(call_row[9], "'-danger")
        self.assertEqual(call_row[10], "'@formula")

    def test_summary_and_export_respect_branch_scope(self):
        from apps.branch.models import Branch
        from apps.lead.models import LeadCall

        other_branch = Branch.objects.create(
            name='Dharan', code='DHR',
        )

        LeadCall.objects.create(
            lead=self.lead,
            call_timestamp=self.recent_call.call_timestamp,
            direction='inbound',
            caller='Other Branch Caller',
            phone_number='9800000000',
            duration_seconds=120,
            branch=other_branch,
        )

        branch_user = User.objects.create_user(
            email='branch@test.com',
            name='Branch User',
            password='pass',
            branch=self.branch,
        )

        self.client.force_authenticate(branch_user)

        # Summary
        summary = self.client.get('/api/calls/summary/')
        self.assertEqual(summary.status_code, status.HTTP_200_OK)
        self.assertEqual(summary.data['total_calls'], 2)

        # List
        list_response = self.client.get('/api/calls/')
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(list_response.data['count'], 2)

        callers = [
            call['caller']
            for call in list_response.data['results']
        ]
        self.assertNotIn('Other Branch Caller', callers)

        # CSV export
        export = self.client.get('/api/calls/export_csv/')
        self.assertEqual(export.status_code, status.HTTP_200_OK)

        content = export.content.decode('utf-8-sig')
        self.assertNotIn('Other Branch Caller', content)

    def test_staff_can_view_all_calls_and_filter_by_branch(self):
        from apps.branch.models import Branch
        from apps.lead.models import LeadCall

        other_branch = Branch.objects.create(
            name='Dharan',
            code='DHR',
        )

        LeadCall.objects.create(
            lead=self.lead,
            call_timestamp=self.recent_call.call_timestamp,
            direction='inbound',
            caller='Dharan Caller',
            phone_number='9800000000',
            duration_seconds=120,
            branch=other_branch,
        )

        self.client.force_authenticate(self.admin)

        response = self.client.get('/api/calls/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 3)

        response = self.client.get(
            f'/api/calls/?branch_id={other_branch.id}'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(
            response.data['results'][0]['caller'],
            'Dharan Caller',
        )

        summary = self.client.get(
            f'/api/calls/summary/?branch_id={other_branch.id}'
        )
        self.assertEqual(summary.status_code, status.HTTP_200_OK)
        self.assertEqual(summary.data['total_calls'], 1)

        export = self.client.get(
            f'/api/calls/export_csv/?branch_id={other_branch.id}'
        )
        self.assertEqual(export.status_code, status.HTTP_200_OK)

        content = export.content.decode('utf-8-sig')
        self.assertIn('Dharan Caller', content)
        self.assertNotIn('Counselor Nine', content)

    def test_summary_uses_fixed_number_of_queries(self):
        with self.assertNumQueries(3):
            response = self.client.get('/api/calls/summary/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_csv_export_uses_single_query(self):
        with self.assertNumQueries(1):
            response = self.client.get('/api/calls/export_csv/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)