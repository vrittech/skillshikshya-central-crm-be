import csv
import io
from datetime import datetime
from unittest.mock import patch

from django.db import connection
from django.contrib.auth.models import Permission
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from apps.account.models import User
from apps.branch.models import Branch
from apps.lead.call_export import CALL_COLUMNS
from apps.lead.models import Lead, LeadCall


class CallReportingTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.dharan = Branch.objects.create(name='Dharan', code='DHR')
        cls.biratnagar = Branch.objects.create(name='Biratnagar', code='BRT')
        cls.staff = User.objects.create_user(email='staff@example.test', name='Staff', is_staff=True)
        cls.sita = User.objects.create_user(
            email='sita@example.test', name='सीता', branch=cls.dharan,
        )
        cls.unassigned = User.objects.create_user(email='unassigned@example.test', name='Unassigned')
        cls.lead = Lead.objects.create(full_name='सुमन श्रेष्ठ', phone='9800000000', branch=cls.dharan)
        cls.other_lead = Lead.objects.create(full_name='Other lead', phone='9810000000', branch=cls.biratnagar)
        cls.first = cls.make_call('2026-10-28T17:15:00+00:00', 259)
        cls.second = cls.make_call('2026-10-28T20:00:00+00:00', 0, direction='inbound')
        cls.third = cls.make_call(
            '2026-10-29T18:14:59.999999+00:00', 41, created_by=None,
            notes='=HYPERLINK("https://example.test", "test")\nA second line, too',
        )
        cls.foreign = cls.make_call(
            '2026-10-30T00:00:00+00:00', 600, lead=cls.other_lead,
            branch=cls.biratnagar, created_by=cls.staff,
        )

    @classmethod
    def make_call(cls, timestamp, seconds, **kwargs):
        values = dict(
            lead=cls.lead, branch=cls.dharan, created_by=cls.sita,
            caller='सीता', phone_number='9800000000', direction='outbound',
            call_timestamp=datetime.fromisoformat(timestamp), duration_seconds=seconds,
        )
        values.update(kwargs)
        return LeadCall.objects.create(**values)

    def setUp(self):
        self.client.force_authenticate(self.staff)

    def export_rows(self, params=None):
        response = self.client.get('/api/calls/export/', params or {})
        self.assertEqual(response.status_code, 200)
        content = b''.join(response.streaming_content)
        self.assertTrue(content.startswith(b'\xef\xbb\xbf'))
        return response, list(csv.reader(io.StringIO(content.decode('utf-8-sig'))))

    def test_summary_totals_include_zero_duration(self):
        data = self.client.get('/api/calls/summary/', {'branch_id': self.dharan.pk}).data
        self.assertEqual(data['total_calls'], 3)
        self.assertEqual((data['inbound'], data['outbound']), (1, 2))
        self.assertEqual(data['total_seconds'], 300)
        self.assertEqual(data['total_formatted'], '00:05:00')
        self.assertEqual(data['avg_seconds'], 100)
        self.assertEqual(data['connected_calls'], 2)
        self.assertAlmostEqual(data['connect_rate'], 2 / 3)
        self.assertEqual(data['by_user'], [
            {'user_id': self.sita.pk, 'name': 'सीता', 'calls': 2, 'seconds': 259},
            {'user_id': None, 'name': 'Unknown', 'calls': 1, 'seconds': 41},
        ])

    def test_day_buckets_use_nepal_time(self):
        data = self.client.get('/api/calls/summary/', {'branch_id': self.dharan.pk}).data
        self.assertEqual(
            [(str(day['date']), day['calls'], day['seconds']) for day in data['by_day']],
            [('2026-10-28', 1, 259), ('2026-10-29', 2, 41)],
        )

    def test_last_microsecond_of_nepal_day_is_included(self):
        params = {
            'call_timestamp__gte': '2026-10-29T00:00:00+05:45',
            'call_timestamp__lte': '2026-10-29T23:59:59.999999+05:45',
        }
        listing = self.client.get('/api/calls/', params).data
        summary = self.client.get('/api/calls/summary/', params).data
        _, rows = self.export_rows(params)
        self.assertEqual([row['id'] for row in listing['results']], [self.third.pk, self.second.pk])
        self.assertEqual(summary['total_calls'], 2)
        self.assertEqual(len(rows) - 1, 2)

    def test_empty_summary_and_export(self):
        params = {'search': 'no-such-lead'}
        data = self.client.get('/api/calls/summary/', params).data
        for key in ('total_calls', 'inbound', 'outbound', 'total_seconds', 'avg_seconds', 'connected_calls', 'connect_rate'):
            self.assertEqual(data[key], 0, key)
        self.assertEqual(data['total_formatted'], '00:00:00')
        self.assertEqual(data['by_day'], [])
        self.assertEqual(data['by_user'], [])
        _, rows = self.export_rows(params)
        self.assertEqual(rows, [CALL_COLUMNS])

    def test_summary_and_export_share_list_filters(self):
        for params in (
            {'direction': 'inbound'}, {'created_by': self.sita.pk},
            {'lead': self.other_lead.pk}, {'search': 'Other lead'},
            {'search': '9810000000'}, {'search': 'HYPERLINK'},
            {'branch_id': self.dharan.pk, 'direction': 'outbound', 'search': 'सीता'},
        ):
            with self.subTest(params=params):
                listing = self.client.get('/api/calls/', params).data
                summary = self.client.get('/api/calls/summary/', params).data
                _, rows = self.export_rows(params)
                self.assertEqual(summary['total_calls'], listing['count'])
                self.assertEqual(summary['total_calls'], len(rows) - 1)
                self.assertEqual(summary['total_seconds'], sum(row['duration_seconds'] for row in listing['results']))

    def test_non_staff_cannot_change_branch_for_list_summary_or_export(self):
        self.client.force_authenticate(self.sita)
        for params in ({}, {'branch_id': self.biratnagar.pk}):
            listing = self.client.get('/api/calls/', params).data
            summary = self.client.get('/api/calls/summary/', params).data
            _, rows = self.export_rows(params)
            self.assertEqual(listing['count'], 3)
            self.assertNotIn(self.foreign.pk, [row['id'] for row in listing['results']])
            self.assertEqual(summary['total_calls'], 3)
            self.assertEqual(summary['total_seconds'], 300)
            self.assertNotIn(self.staff.pk, [row['user_id'] for row in summary['by_user']])
            self.assertEqual(len(rows) - 1, 3)
            self.assertNotIn('Other lead', str(rows))
        self.assertEqual(self.client.get(f'/api/calls/{self.foreign.pk}/').status_code, 404)

    def test_staff_all_branches_and_explicit_branch(self):
        for params, expected in (({}, 4), ({'branch_id': self.biratnagar.pk}, 1)):
            self.assertEqual(self.client.get('/api/calls/', params).data['count'], expected)
            self.assertEqual(self.client.get('/api/calls/summary/', params).data['total_calls'], expected)
            _, rows = self.export_rows(params)
            self.assertEqual(len(rows) - 1, expected)

    def test_no_branch_retains_existing_unrestricted_behavior(self):
        self.client.force_authenticate(self.unassigned)
        self.assertEqual(self.client.get('/api/calls/').data['count'], 4)
        self.assertEqual(self.client.get('/api/calls/summary/').data['total_calls'], 4)
        _, rows = self.export_rows()
        self.assertEqual(len(rows) - 1, 4)

    def test_endpoints_require_authentication(self):
        self.client.force_authenticate(None)
        for path in ('/api/calls/', '/api/calls/summary/', '/api/calls/export/'):
            self.assertIn(self.client.get(path).status_code, (401, 403))

    def test_logged_call_inherits_lead_branch_and_is_visible_to_counselor(self):
        self.sita.user_permissions.add(Permission.objects.get(codename='add_leadcall'))
        self.sita.call_seconds_balance = 1000
        self.sita.save(update_fields=['call_seconds_balance'])
        self.client.force_authenticate(self.sita)
        response = self.client.post(f'/api/leads/{self.lead.pk}/calls/', {
            'call_timestamp': '2026-10-29T12:00:00+05:45',
            'caller': 'सीता', 'phone_number': '9800000000',
            'duration_input': '4:19', 'branch': self.biratnagar.pk,
        }, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['branch'], self.dharan.pk)
        self.assertEqual(response.data['duration_seconds'], 259)
        self.assertEqual(response.data['remaining_seconds'], 741)
        self.assertEqual(self.client.get('/api/calls/').data['count'], 4)

    def test_counselor_cannot_log_a_call_on_another_branch_lead(self):
        self.sita.user_permissions.add(Permission.objects.get(codename='add_leadcall'))
        self.client.force_authenticate(self.sita)
        response = self.client.post(f'/api/leads/{self.other_lead.pk}/calls/', {
            'call_timestamp': '2026-10-29T12:00:00+05:45',
            'caller': 'सीता', 'phone_number': '9800000000', 'duration_input': '0',
        }, format='json')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(LeadCall.objects.count(), 4)

    def test_csv_headers_nepali_text_dates_and_escaping(self):
        with patch('apps.lead.views.timezone.now', return_value=datetime.fromisoformat('2026-10-28T20:00:00+00:00')):
            response, rows = self.export_rows({'lead': self.lead.pk, 'ordering': 'call_timestamp'})
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        self.assertEqual(response['Content-Disposition'], 'attachment; filename="calls-2026-10-29.csv"')
        self.assertEqual(rows[0], CALL_COLUMNS)
        self.assertEqual(rows[1][0], '2026-10-28 23:00:00')
        self.assertEqual(rows[1][2], 'सुमन श्रेष्ठ')
        self.assertEqual(rows[1][5:9], ['सीता', 'सीता', '04:19', '259'])
        self.assertEqual(rows[3][10], "'" + self.third.notes)

    def test_all_text_columns_neutralize_formulas(self):
        for value in ('=1+1', '+123', '-123', '@SUM(A1)', '  =1', '\t=1', '\r=1', '\n=1'):
            with self.subTest(value=value):
                LeadCall.objects.filter(pk=self.first.pk).update(notes=value, caller=value, outcome=value)
                Lead.objects.filter(pk=self.lead.pk).update(full_name=value)
                _, rows = self.export_rows({'created_by': self.sita.pk, 'direction': 'outbound'})
                for column in (2, 5, 9, 10):
                    self.assertEqual(rows[1][column], "'" + value)

    def test_export_ignores_pagination_and_respects_sort(self):
        _, rows = self.export_rows({'page': 2, 'page_size': 1, 'ordering': 'duration_seconds'})
        self.assertEqual([int(row[8]) for row in rows[1:]], [0, 41, 259, 600])

    def test_list_duration_and_stable_ties(self):
        LeadCall.objects.filter(pk=self.second.pk).update(call_timestamp=self.first.call_timestamp)
        data = self.client.get('/api/calls/', {'ordering': 'call_timestamp'}).data
        self.assertEqual([row['id'] for row in data['results'][:2]], [self.second.pk, self.first.pk])
        self.assertEqual(data['results'][1]['duration_formatted'], '04:19')

    def test_top_callers_limited_to_ten(self):
        for i in range(12):
            user = User.objects.create_user(email=f'caller{i}@example.test', name=f'Caller {i}')
            self.make_call('2026-10-29T00:00:00+00:00', 1, created_by=user)
        data = self.client.get('/api/calls/summary/').data
        self.assertEqual(len(data['by_user']), 10)
        self.assertEqual(data['by_user'][0]['user_id'], self.sita.pk)

    def test_query_counts_stay_fixed_with_twenty_thousand_calls(self):
        def query_counts():
            counts = []
            for path in ('/api/calls/', '/api/calls/summary/', '/api/calls/export/'):
                with CaptureQueriesContext(connection) as captured:
                    response = self.client.get(path, {'page_size': 20})
                    self.assertEqual(response.status_code, 200)
                    if response.streaming:
                        for _ in response.streaming_content:
                            pass
                counts.append(len(captured))
            return counts

        before = query_counts()
        LeadCall.objects.bulk_create([
            LeadCall(
                lead=self.lead, branch=self.dharan, created_by=self.sita,
                caller='सीता', phone_number='9800000000',
                call_timestamp=self.first.call_timestamp, duration_seconds=60,
            ) for _ in range(20000)
        ], batch_size=1000)
        self.assertEqual(query_counts(), before)
        self.assertEqual(before, [2, 3, 1])
