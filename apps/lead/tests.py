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


class LogCallBranchTests(APITestCase):
    def setUp(self):
        from apps.branch.models import Branch
        from django.contrib.auth.models import Permission
        self.branch = Branch.objects.create(name='Biratnagar', code='BRT')
        self.other_branch = Branch.objects.create(name='Dharan', code='DHR')
        self.counselor = User.objects.create_user(
            email='sita@test.com', name='Sita', password='x',
            branch=self.branch, call_seconds_balance=3600,
        )
        self.counselor.user_permissions.add(Permission.objects.get(codename='add_leadcall'))
        self.client.force_authenticate(self.counselor)

    def _log_call(self, lead, **extra):
        return self.client.post(f'/api/leads/{lead.id}/calls/', {
            'call_timestamp': '2026-10-28T05:00:00Z', 'caller': 'Sita',
            'phone_number': '9811111111', 'duration_input': '4:19', **extra,
        }, format='json')

    def test_call_takes_the_lead_branch(self):
        lead = Lead.objects.create(full_name='Lead', phone='9811111111', branch=self.branch)
        response = self._log_call(lead)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['branch'], self.branch.id)
        self.assertEqual(response.data['duration_seconds'], 259)
        self.assertEqual(self.client.get('/api/calls/').data['count'], 1)

    def test_client_cannot_file_a_call_under_another_branch(self):
        lead = Lead.objects.create(full_name='Lead', phone='9811111111', branch=self.branch)
        response = self._log_call(lead, branch=self.other_branch.id)
        self.assertEqual(response.data['branch'], self.branch.id)

    def test_lead_without_branch_falls_back_to_the_user_branch(self):
        lead = Lead.objects.create(full_name='Lead', phone='9811111111')
        response = self._log_call(lead)
        self.assertEqual(response.data['branch'], self.branch.id)

    def test_unknown_lead_returns_404(self):
        response = self.client.post('/api/leads/999999/calls/', {
            'call_timestamp': '2026-10-28T05:00:00Z', 'caller': 'Sita',
            'phone_number': '9811111111', 'duration_seconds': 10,
        }, format='json')
        self.assertEqual(response.status_code, 404)


class CallEndpointTestBase(APITestCase):
    """Two branches with one staff user, one counselor each and a lead each."""

    def setUp(self):
        from apps.branch.models import Branch
        self.branch_x = Branch.objects.create(name='Biratnagar', code='BRT')
        self.branch_y = Branch.objects.create(name='Dharan', code='DHR')
        self.admin = User.objects.create_user(
            email='admin@test.com', name='Admin', password='x', is_staff=True,
        )
        self.user_x = User.objects.create_user(
            email='x@test.com', name='Sita', password='x', branch=self.branch_x,
        )
        self.user_y = User.objects.create_user(
            email='y@test.com', name='Ramesh', password='x', branch=self.branch_y,
        )
        self.lead_x = Lead.objects.create(full_name='Lead X', phone='9811111111', branch=self.branch_x)
        self.lead_y = Lead.objects.create(full_name='Lead Y', phone='9822222222', branch=self.branch_y)
        self.client.force_authenticate(self.admin)

    def make_call(self, when='2026-10-20T06:00:00Z', seconds=60, lead=None, user=None, **extra):
        from apps.lead.models import LeadCall
        from django.utils.dateparse import parse_datetime
        lead = lead or self.lead_x
        user = user or self.user_x
        defaults = dict(
            lead=lead, call_timestamp=parse_datetime(when), direction='outbound',
            caller=user.name, phone_number=lead.phone, duration_seconds=seconds,
            created_by=user, branch=lead.branch,
        )
        defaults.update(extra)
        return LeadCall.objects.create(**defaults)


class GlobalCallSummaryTests(CallEndpointTestBase):
    def test_totals_average_and_connect_rate_include_a_zero_second_call(self):
        self.make_call(seconds=100)
        self.make_call(seconds=200, direction='inbound')
        self.make_call(seconds=0)
        self.make_call(seconds=300)
        data = self.client.get('/api/calls/summary/').data
        self.assertEqual(data['total_calls'], 4)
        self.assertEqual(data['inbound'], 1)
        self.assertEqual(data['outbound'], 3)
        self.assertEqual(data['total_seconds'], 600)
        self.assertEqual(data['total_formatted'], '00:10:00')
        self.assertEqual(data['avg_seconds'], 150)
        self.assertEqual(data['connected_calls'], 3)
        self.assertEqual(data['connect_rate'], 0.75)

    def test_days_are_counted_in_nepal_time(self):
        self.make_call(when='2026-10-28T20:00:00Z', seconds=10)  # 01:45 on Oct 29 NPT
        self.make_call(when='2026-10-28T17:15:00Z', seconds=20)  # 23:00 on Oct 28 NPT
        self.make_call(when='2026-10-28T18:14:59Z', seconds=30)  # 23:59:59 on Oct 28 NPT
        self.make_call(when='2026-10-28T18:15:00Z', seconds=40)  # 00:00:00 on Oct 29 NPT
        by_day = self.client.get('/api/calls/summary/').data['by_day']
        self.assertEqual(by_day, [
            {'date': '2026-10-28', 'calls': 2, 'seconds': 50},
            {'date': '2026-10-29', 'calls': 2, 'seconds': 50},
        ])

    def test_empty_result_returns_zeros(self):
        data = self.client.get('/api/calls/summary/').data
        self.assertEqual(data, {
            'total_calls': 0, 'inbound': 0, 'outbound': 0, 'total_seconds': 0,
            'total_formatted': '00:00:00', 'avg_seconds': 0, 'connected_calls': 0,
            'connect_rate': 0, 'by_day': [], 'by_user': [],
        })

    def test_by_user_groups_sorts_and_labels_unknown(self):
        self.make_call(user=self.user_x, seconds=10)
        self.make_call(user=self.user_x, seconds=20)
        self.make_call(user=self.user_y, lead=self.lead_y, seconds=30)
        from apps.lead.models import LeadCall
        LeadCall.objects.filter(duration_seconds=30).update(created_by=None)
        by_user = self.client.get('/api/calls/summary/').data['by_user']
        self.assertEqual(by_user, [
            {'user_id': self.user_x.id, 'name': 'Sita', 'calls': 2, 'seconds': 30},
            {'user_id': None, 'name': 'Unknown', 'calls': 1, 'seconds': 30},
        ])

    def test_by_user_is_limited_to_the_top_ten(self):
        for i in range(11):
            user = User.objects.create_user(email=f'u{i}@test.com', name=f'User {i}', password='x')
            self.make_call(user=user)
        self.assertEqual(len(self.client.get('/api/calls/summary/').data['by_user']), 10)

    def test_total_matches_the_list_count_for_the_same_filters(self):
        self.make_call(seconds=10, notes='needle')
        self.make_call(seconds=20, direction='inbound', notes='needle')
        self.make_call(seconds=30, notes='other')
        self.make_call(when='2026-09-01T06:00:00Z', notes='needle')
        for query in [
            '', '?direction=inbound', '?search=needle', f'?created_by={self.user_x.id}',
            '?call_timestamp__gte=2026-10-01T00:00:00%2B05:45', f'?branch_id={self.branch_y.id}',
        ]:
            summary = self.client.get(f'/api/calls/summary/{query}').data
            listing = self.client.get(f'/api/calls/{query}').data
            self.assertEqual(summary['total_calls'], listing['count'], msg=query)

    def test_list_ordering_does_not_split_the_groups(self):
        self.make_call(when='2026-10-20T04:00:00Z')
        self.make_call(when='2026-10-20T05:00:00Z')
        data = self.client.get('/api/calls/summary/?ordering=call_timestamp').data
        self.assertEqual(len(data['by_day']), 1)
        self.assertEqual(data['by_day'][0]['calls'], 2)

    def test_end_of_day_filter_keeps_the_last_second_in_nepal_time(self):
        last = self.make_call(when='2026-10-28T18:14:59Z')  # 23:59:59 NPT
        self.make_call(when='2026-10-28T18:15:00Z')  # next day NPT
        response = self.client.get('/api/calls/', {
            'call_timestamp__gte': '2026-10-28T00:00:00+05:45',
            'call_timestamp__lte': '2026-10-28T23:59:59.999999+05:45',
        })
        self.assertEqual([r['id'] for r in response.data['results']], [last.id])

    def test_list_includes_duration_formatted(self):
        self.make_call(seconds=259)
        row = self.client.get('/api/calls/').data['results'][0]
        self.assertEqual(row['duration_formatted'], '04:19')
