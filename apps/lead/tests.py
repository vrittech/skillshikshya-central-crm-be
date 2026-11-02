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

    def test_export_csv(self):
        from apps.lead.models import LeadCall
        # Add formula injection test note
        LeadCall.objects.filter(id=self.recent_call.id).update(notes='=1+1', caller='राम श्रेष्ठ')
        response = self.client.get('/api/calls/export_csv/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response['Content-Type'])
        self.assertIn('attachment; filename="calls-', response['Content-Disposition'])
        # Verify UTF-8 BOM
        self.assertTrue(response.content.startswith(b'\xef\xbb\xbf'))
        content = response.content.decode('utf-8')
        # Verify exact column order
        expected_header = 'Date & time (NPT),Direction,Lead,Reference,Phone,Caller,Logged by,Duration (MM:SS),Seconds,Outcome,Notes,Recording'
        self.assertIn(expected_header, content)
        # Verify Nepali name rendered properly
        self.assertIn('राम श्रेष्ठ', content)
        # Verify formula injection mitigation: note starting with = is prefixed with '
        self.assertIn("'=1+1", content)

    def test_branch_scoping_for_non_staff(self):
        self.test_branch_scoping_non_staff_never_sees_other_branch_calls()

    def test_branch_scoping_non_staff_never_sees_other_branch_calls(self):
        from apps.branch.models import Branch
        from apps.lead.models import Lead, LeadCall

        other_branch = Branch.objects.create(name='Dharan', code='DHR')
        other_lead = Lead.objects.create(full_name='Dharan Lead', phone='9899999999', branch=other_branch)
        dharan_call = LeadCall.objects.create(
            lead=other_lead, call_timestamp=self.recent_call.call_timestamp,
            direction='outbound', caller='Dharan Counselor',
            duration_seconds=120, branch=other_branch,
        )

        dharan_user = User.objects.create_user(
            email='counselor_dharan@test.com', name='Dharan Counselor User', password='x',
            branch=other_branch, is_staff=False,
        )
        self.client.force_authenticate(dharan_user)

        # 1. List endpoint: Dharan user only sees Dharan calls, never Biratnagar calls
        list_resp = self.client.get('/api/calls/')
        self.assertEqual(list_resp.status_code, 200)
        call_ids = [r['id'] for r in list_resp.data['results']]
        self.assertIn(dharan_call.id, call_ids)
        self.assertNotIn(self.recent_call.id, call_ids)
        self.assertNotIn(self.old_call.id, call_ids)

        # 2. Summary endpoint: Dharan user only sees Dharan metrics
        sum_resp = self.client.get('/api/calls/summary/')
        self.assertEqual(sum_resp.status_code, 200)
        self.assertEqual(sum_resp.data['total_calls'], 1)
        self.assertEqual(sum_resp.data['total_seconds'], 120)
        self.assertEqual(sum_resp.data['top_callers'][0]['caller'], 'Dharan Counselor')

        # 3. Export endpoint: Dharan user only exports Dharan calls
        exp_resp = self.client.get('/api/calls/export_csv/')
        self.assertEqual(exp_resp.status_code, 200)
        exp_content = exp_resp.content.decode('utf-8')
        self.assertIn('Dharan Lead', exp_content)
        self.assertNotIn('Call Person', exp_content)

        # 4. Detail endpoint: Dharan user cannot retrieve Biratnagar call directly by ID (404)
        detail_resp = self.client.get(f'/api/calls/{self.recent_call.id}/')
        self.assertEqual(detail_resp.status_code, 404)

        # 5. Query param spoofing: Dharan user passing ?branch_id=Biratnagar is ignored
        spoofed_resp = self.client.get(f'/api/calls/?branch_id={self.branch.id}')
        self.assertEqual(spoofed_resp.status_code, 200)
        spoofed_ids = [r['id'] for r in spoofed_resp.data['results']]
        self.assertNotIn(self.recent_call.id, spoofed_ids)
        self.assertIn(dharan_call.id, spoofed_ids)

        # 6. Logging call: Dharan counselor cannot log a call to a Biratnagar lead (403)
        log_resp = self.client.post(
            f'/api/leads/{self.lead.id}/calls/',
            {'call_timestamp': self.recent_call.call_timestamp.isoformat(), 'caller': 'Dharan Counselor', 'duration_seconds': 60},
            format='json',
        )
        self.assertEqual(log_resp.status_code, 403)

    def test_staff_sees_all_branches_and_narrows_with_branch_id(self):
        from apps.branch.models import Branch
        from apps.lead.models import Lead, LeadCall

        other_branch = Branch.objects.create(name='Dharan', code='DHR')
        other_lead = Lead.objects.create(full_name='Dharan Lead', phone='9899999999', branch=other_branch)
        dharan_call = LeadCall.objects.create(
            lead=other_lead, call_timestamp=self.recent_call.call_timestamp,
            direction='outbound', caller='Dharan Counselor',
            duration_seconds=120, branch=other_branch,
        )

        self.client.force_authenticate(self.admin)  # is_staff=True

        # Staff sees all branches by default
        all_resp = self.client.get('/api/calls/')
        all_ids = [r['id'] for r in all_resp.data['results']]
        self.assertIn(dharan_call.id, all_ids)
        self.assertIn(self.recent_call.id, all_ids)

        # Staff narrows by branch_id
        filtered_resp = self.client.get(f'/api/calls/?branch_id={self.branch.id}')
        filtered_ids = [r['id'] for r in filtered_resp.data['results']]
        self.assertIn(self.recent_call.id, filtered_ids)
        self.assertNotIn(dharan_call.id, filtered_ids)

        # Staff summary narrows by branch_id
        filtered_sum = self.client.get(f'/api/calls/summary/?branch_id={other_branch.id}')
        self.assertEqual(filtered_sum.data['total_calls'], 1)
        self.assertEqual(filtered_sum.data['top_callers'][0]['caller'], 'Dharan Counselor')

        # Staff export narrows by branch_id
        filtered_exp = self.client.get(f'/api/calls/export_csv/?branch_id={other_branch.id}')
        exp_content = filtered_exp.content.decode('utf-8')
        self.assertIn('Dharan Lead', exp_content)
        self.assertNotIn('Call Person', exp_content)

    def test_summary(self):
        response = self.client.get('/api/calls/summary/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total_calls'], 2)
        self.assertEqual(response.data['outbound_calls'], 1)
        self.assertEqual(response.data['inbound_calls'], 1)
        self.assertEqual(response.data['total_seconds'], 330)
        self.assertEqual(response.data['connected_calls'], 2)
        self.assertEqual(response.data['connect_rate'], 100.0)
        self.assertEqual(len(response.data['top_callers']), 2)
        self.assertEqual(response.data['top_callers'][0]['caller'], 'Counselor Nine')
        self.assertTrue(len(response.data['calls_per_day']) >= 1)

    def test_summary_filter_direction(self):
        response = self.client.get('/api/calls/summary/?direction=inbound')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total_calls'], 1)
        self.assertEqual(response.data['inbound_calls'], 1)
        self.assertEqual(response.data['outbound_calls'], 0)
        self.assertEqual(response.data['total_seconds'], 60)
        self.assertEqual(response.data['connect_rate'], 100.0)

    def test_summary_empty_state(self):
        response = self.client.get('/api/calls/summary/?search=NonExistentUser123')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total_calls'], 0)
        self.assertEqual(response.data['inbound_calls'], 0)
        self.assertEqual(response.data['outbound_calls'], 0)
        self.assertEqual(response.data['total_seconds'], 0)
        self.assertEqual(response.data['avg_duration_seconds'], 0)
        self.assertEqual(response.data['connected_calls'], 0)
        self.assertEqual(response.data['connect_rate'], 0.0)
        self.assertEqual(response.data['calls_per_day'], [])
        self.assertEqual(response.data['top_callers'], [])
