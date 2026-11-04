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


    def test_branch_scoping_for_non_staff_list(self):
        """Non-staff user in Branch X cannot see Branch Y calls in list endpoint."""
        from apps.branch.models import Branch
        from apps.lead.models import LeadCall
        
        branch_y = Branch.objects.create(name='Dharan', code='DHR')
        dharan_user = User.objects.create_user(
            email='dharan@test.com', name='Dharan User', password='x',
            branch=branch_y, is_staff=False
        )
        
        # Ensure recent_call belongs to Branch X (not Branch Y)
        LeadCall.objects.filter(id=self.recent_call.id).update(branch=self.branch)
        
        self.client.force_authenticate(dharan_user)
        response = self.client.get('/api/calls/')
        
        self.assertEqual(response.status_code, 200)
        returned_ids = [r['id'] for r in response.data['results']]
        self.assertNotIn(self.recent_call.id, returned_ids)


    def test_branch_scoping_for_non_staff_summary(self):
        """Non-staff user in Branch X cannot see Branch Y metrics in summary endpoint."""
        from apps.branch.models import Branch
        from apps.lead.models import LeadCall
        
        branch_y = Branch.objects.create(name='Dharan', code='DHR')
        dharan_user = User.objects.create_user(
            email='dharan_summary@test.com', name='Dharan Summary User', password='x',
            branch=branch_y, is_staff=False
        )
        
        # Assign existing test calls to Branch X
        LeadCall.objects.filter(id=self.recent_call.id).update(branch=self.branch)
        
        self.client.force_authenticate(dharan_user)
        response = self.client.get('/api/calls/summary/')
        
        self.assertEqual(response.status_code, 200)
        # Summary total_calls count must be 0 for Dharan user
        self.assertEqual(response.data.get('total_calls', 0), 0)


    def test_branch_scoping_for_non_staff_export(self):
        """Non-staff user in Branch X cannot see Branch Y rows in CSV export."""
        from apps.branch.models import Branch
        from apps.lead.models import LeadCall
        
        branch_y = Branch.objects.create(name='Dharan', code='DHR')
        dharan_user = User.objects.create_user(
            email='dharan_export@test.com', name='Dharan Export User', password='x',
            branch=branch_y, is_staff=False
        )
        
        # Assign call to Branch X
        LeadCall.objects.filter(id=self.recent_call.id).update(branch=self.branch)
        
        self.client.force_authenticate(dharan_user)
        response = self.client.get('/api/calls/export/')
        
        self.assertEqual(response.status_code, 200)
        csv_content = response.content.decode('utf-8')
        # Prove that phone number/call reference of Branch X does not leak into Dharan user's CSV
        self.assertNotIn(self.recent_call.phone_number, csv_content)    


class GlobalCallSummaryAndExportTests(APITestCase):
    def setUp(self):
        from datetime import timedelta
        from apps.branch.models import Branch
        from django.utils import timezone as tz
        from apps.lead.models import LeadCall

        self.admin = User.objects.create_user(
            email='admin_summary@test.com', name='Admin Summary', password='pass', is_staff=True,
        )
        self.counselor = User.objects.create_user(
            email='c9_summary@test.com', name='Counselor Nine Summary', password='pass',
            is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.branch = Branch.objects.create(name='Biratnagar', code='BRT')
        self.lead = Lead.objects.create(full_name='Call Person', phone='9811111111')
        
        now = tz.now()
        # Connected call (> 0 seconds)
        self.recent_call = LeadCall.objects.create(
            lead=self.lead, call_timestamp=now, direction='outbound',
            caller='Counselor Nine Summary', phone_number='9811111111',
            duration_seconds=270, outcome='interested',
            notes='Regular note',
            created_by=self.counselor, branch=self.branch,
        )
        # Unconnected call (0 seconds)
        self.old_call = LeadCall.objects.create(
            lead=self.lead, call_timestamp=now - timedelta(days=10),
            direction='inbound', caller='Someone Else',
            phone_number='9822222222', duration_seconds=0,
            notes='=HYPERLINK("http://malicious.com")',  # Formula injection test case
            created_by=self.admin, branch=self.branch,
        )

    def test_summary_endpoint_totals_and_aggregations(self):
        response = self.client.get('/api/calls/summary/')
        self.assertEqual(response.status_code, 200)
        data = response.data

        # Verify exact aggregations based on setUp fixtures (2 calls total: 1 inbound, 1 outbound)
        self.assertEqual(data['total_calls'], 2)
        self.assertEqual(data['inbound'], 1)
        self.assertEqual(data['outbound'], 1)
        self.assertEqual(data['total_seconds'], 270)  # 270s + 0s
        self.assertEqual(data['connected_calls'], 1)  # Only 1 call has duration > 0
        self.assertEqual(data['connect_rate'], 0.5)     # 1 / 2 connected rate
        self.assertGreaterEqual(len(data['by_day']), 1)
        self.assertGreaterEqual(len(data['by_user']), 1)

    def test_export_csv_endpoint_format_and_formula_safety(self):
        response = self.client.get('/api/calls/export/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        self.assertTrue(response['Content-Disposition'].startswith('attachment; filename="calls-'))

        # Decode content with utf-8-sig to handle the UTF-8 BOM (\ufeff) properly
        content = response.content.decode('utf-8-sig')

        # Verify header structure
        self.assertIn("Date & time (NPT)", content)
        self.assertIn("Duration (MM:SS)", content)

        # Verify formula injection safety guard neutralizes '=' with a leading apostrophe
        self.assertIn("'=HYPERLINK", content)