import csv
import io
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from apps.branch.models import Branch
from apps.lead.models import Lead, LeadCall


class CallsReportingTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.branch = Branch.objects.create(name='Dharan', code='DHR')
        cls.other_branch = Branch.objects.create(name='Biratnagar', code='BRT')
        cls.staff = User.objects.create_user(email='staff@example.test', name='Manager', is_staff=True)
        cls.user = User.objects.create_user(email='sita@example.test', name='Sita', branch=cls.branch)
        cls.other_user = User.objects.create_user(email='other@example.test', name='Other', branch=cls.other_branch)
        cls.lead = Lead.objects.create(full_name='सीता परीक्षण', phone='9800000000', branch=cls.branch)
        cls.other_lead = Lead.objects.create(full_name='Other lead', phone='9810000000', branch=cls.other_branch)
        cls.start = datetime(2026, 8, 15, tzinfo=ZoneInfo('Asia/Kathmandu'))
        cls.first = LeadCall.objects.create(
            lead=cls.lead, branch=cls.branch, created_by=cls.user,
            call_timestamp=cls.start, caller='Sita', phone_number='9800000000',
            direction='outbound', duration_seconds=259, notes='Needs fees, then a callback\nTomorrow',
        )
        cls.late = LeadCall.objects.create(
            lead=cls.lead, branch=cls.branch, created_by=cls.user,
            call_timestamp=cls.start + timedelta(hours=23, minutes=59, seconds=59, microseconds=999999),
            caller='Sita', phone_number='9800000000', direction='inbound', duration_seconds=0,
        )
        cls.other = LeadCall.objects.create(
            lead=cls.other_lead, branch=cls.other_branch, created_by=cls.other_user,
            call_timestamp=cls.start + timedelta(days=1), caller='Other',
            phone_number='9810000000', direction='outbound', duration_seconds=60,
        )

    def setUp(self):
        self.client.force_authenticate(self.staff)

    def export(self, params=None):
        response = self.client.get('/api/calls/export_csv/', params or {})
        self.assertEqual(response.status_code, 200)
        content = b''.join(response.streaming_content)
        return response, content, list(csv.reader(io.StringIO(content.decode('utf-8-sig'))))

    def test_summary_totals_and_nepal_day_buckets(self):
        response = self.client.get('/api/calls/summary/')
        self.assertEqual(response.status_code, 200)
        data = response.data
        self.assertEqual(data['total_calls'], 3)
        self.assertEqual(data['inbound_calls'], 1)
        self.assertEqual(data['outbound_calls'], 2)
        self.assertEqual(data['talk_time_seconds'], 319)
        self.assertAlmostEqual(data['average_duration_seconds'], 319 / 3)
        self.assertEqual(data['connected_calls'], 2)
        self.assertEqual(data['connect_rate'], 66.67)
        self.assertEqual([(str(day['date']), day['count']) for day in data['calls_per_day']], [('2026-08-15', 2), ('2026-08-16', 1)])
        self.assertEqual(data['top_callers'][0], {'created_by': self.user.id, 'created_by__name': 'Sita', 'count': 2})

    def test_combined_filters_match_list_summary_and_export(self):
        params = {'direction': 'outbound', 'created_by': self.user.id, 'search': 'callback', 'from_date': '2026-08-15', 'to_date': '2026-08-15'}
        listed = self.client.get('/api/calls/', params)
        summary = self.client.get('/api/calls/summary/', params)
        _, _, rows = self.export(params)
        self.assertEqual(listed.data['count'], 1)
        self.assertEqual(summary.data['total_calls'], listed.data['count'])
        self.assertEqual(len(rows) - 1, listed.data['count'])

    def test_date_range_includes_whole_last_day(self):
        params = {'from_date': '2026-08-15', 'to_date': '2026-08-15'}
        listed = self.client.get('/api/calls/', params)
        self.assertEqual([row['id'] for row in listed.data['results']], [self.late.id, self.first.id])
        self.assertEqual(self.client.get('/api/calls/summary/', params).data['total_calls'], 2)
        self.assertEqual(len(self.export(params)[2]), 3)

    def test_empty_summary_and_export(self):
        data = self.client.get('/api/calls/summary/', {'search': 'no-such-call'}).data
        for key in ['total_calls', 'talk_time_seconds', 'average_duration_seconds', 'connect_rate', 'connected_calls', 'inbound_calls', 'outbound_calls']:
            self.assertEqual(data[key], 0)
        self.assertEqual(data['calls_per_day'], [])
        self.assertEqual(data['top_callers'], [])
        self.assertEqual(len(self.export({'search': 'no-such-call'})[2]), 1)

    def test_non_staff_cannot_override_branch_on_any_endpoint(self):
        self.client.force_authenticate(self.user)
        for params in [{}, {'branch_id': self.other_branch.id}]:
            with self.subTest(params=params):
                listed = self.client.get('/api/calls/', params)
                self.assertEqual({row['id'] for row in listed.data['results']}, {self.first.id, self.late.id})
                data = self.client.get('/api/calls/summary/', params).data
                self.assertEqual(data['total_calls'], 2)
                self.assertEqual(data['talk_time_seconds'], 259)
                self.assertEqual(len(data['top_callers']), 1)
                _, _, rows = self.export(params)
                self.assertEqual(len(rows), 3)
                self.assertNotIn('Other lead', str(rows))
        self.assertEqual(self.client.get(f'/api/calls/{self.other.id}/').status_code, 404)

    def test_staff_can_see_all_or_filter_one_branch(self):
        for branch, count in [(None, 3), (self.other_branch.id, 1)]:
            params = {'branch_id': branch} if branch else {}
            self.assertEqual(self.client.get('/api/calls/', params).data['count'], count)
            self.assertEqual(self.client.get('/api/calls/summary/', params).data['total_calls'], count)
            self.assertEqual(len(self.export(params)[2]) - 1, count)

    def test_unassigned_non_staff_behavior_is_unchanged(self):
        unassigned = get_user_model().objects.create_user(email='unassigned@example.test', name='Unassigned')
        self.client.force_authenticate(unassigned)
        self.assertEqual(self.client.get('/api/calls/').data['count'], 3)
        self.assertEqual(self.client.get('/api/calls/summary/').data['total_calls'], 3)
        self.assertEqual(len(self.export()[2]) - 1, 3)

    def test_export_unicode_columns_local_time_and_multiline_notes(self):
        response, content, rows = self.export({'ordering': 'call_timestamp'})
        self.assertTrue(content.startswith(b'\xef\xbb\xbf'))
        self.assertRegex(response['Content-Disposition'], r'calls-\d{4}-\d{2}-\d{2}\.csv')
        self.assertEqual(rows[0], ['Date & time (NPT)', 'Direction', 'Lead', 'Reference', 'Phone', 'Caller', 'Logged by', 'Duration (MM:SS)', 'Seconds', 'Outcome', 'Notes', 'Recording'])
        self.assertEqual(rows[1][0], '2026-08-15T00:00:00+05:45')
        self.assertEqual(rows[1][2], 'सीता परीक्षण')
        self.assertEqual(rows[1][7:9], ['04:19', '259'])
        self.assertEqual(rows[1][10], self.first.notes)

    def test_export_treats_formula_prefixes_as_text(self):
        for text in ['=1+1', '+1+1', '-1+1', '@SUM(A1)', '  =1+1', '\t=1+1', '\r=1+1']:
            with self.subTest(text=text):
                LeadCall.objects.filter(pk=self.first.pk).update(notes=text)
                _, _, rows = self.export({'search': 'सीता', 'ordering': 'call_timestamp'})
                self.assertEqual(rows[1][10], "'" + text)

    def test_export_ignores_pagination(self):
        listed = self.client.get('/api/calls/', {'page_size': 1})
        self.assertEqual(len(listed.data['results']), 1)
        self.assertEqual(len(self.export({'page_size': 1, 'page': 2})[2]), 4)

    def test_query_counts_stay_fixed_at_twenty_thousand_calls(self):
        def query_counts():
            counts = []
            for endpoint in ['/api/calls/?page_size=20', '/api/calls/summary/', '/api/calls/export_csv/']:
                with CaptureQueriesContext(connection) as queries:
                    response = self.client.get(endpoint)
                    self.assertEqual(response.status_code, 200)
                    if response.streaming:
                        for chunk in response.streaming_content:
                            pass
                counts.append(len(queries))
            return counts

        before = query_counts()
        LeadCall.objects.bulk_create([
            LeadCall(lead=self.lead, branch=self.branch, created_by=self.user,
                     call_timestamp=self.start, caller='Sita', phone_number='9800000000', duration_seconds=1)
            for _ in range(20000 - LeadCall.objects.count())
        ], batch_size=1000)
        self.assertEqual(query_counts(), before)
        self.assertEqual(before, [2, 3, 1])

    def test_sorting_and_tied_timestamps_are_stable(self):
        LeadCall.objects.filter(pk=self.late.pk).update(call_timestamp=self.start)
        listed = self.client.get('/api/calls/', {'branch_id': self.branch.id}).data['results']
        self.assertEqual([row['id'] for row in listed], [self.late.id, self.first.id])
        listed = self.client.get('/api/calls/', {'ordering': 'duration_seconds'}).data['results']
        self.assertEqual([row['duration_seconds'] for row in listed], [0, 60, 259])

    def test_requires_authentication(self):
        self.client.force_authenticate(None)
        for endpoint in ['/api/calls/', '/api/calls/summary/', '/api/calls/export_csv/']:
            self.assertIn(self.client.get(endpoint).status_code, [401, 403])

    def test_new_calls_inherit_lead_branch(self):
        from django.contrib.auth.models import Permission
        self.user.user_permissions.add(Permission.objects.get(codename='add_leadcall'))
        self.user.call_seconds_balance = 1000
        self.user.save(update_fields=['call_seconds_balance'])
        self.client.force_authenticate(self.user)
        response = self.client.post(f'/api/leads/{self.lead.pk}/calls/', {
            'call_timestamp': self.start.isoformat(), 'caller': 'Sita',
            'phone_number': '9800000000', 'duration_seconds': 259,
            'branch': self.other_branch.pk,
        }, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['branch'], self.branch.pk)
        self.assertEqual(response.data['remaining_seconds'], 741)
        self.assertEqual(self.client.get('/api/calls/').data['count'], 3)

    def test_cannot_log_call_against_another_branch_lead(self):
        from django.contrib.auth.models import Permission
        self.user.user_permissions.add(Permission.objects.get(codename='add_leadcall'))
        self.client.force_authenticate(self.user)
        response = self.client.post(f'/api/leads/{self.other_lead.pk}/calls/', {
            'call_timestamp': self.start.isoformat(), 'caller': 'Sita',
            'phone_number': '9800000000', 'duration_seconds': 0,
        }, format='json')
        self.assertEqual(response.status_code, 404)
