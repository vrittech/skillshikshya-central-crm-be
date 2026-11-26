from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase, APIRequestFactory

from apps.branch.models import Branch
from apps.course.models import Course
from apps.lead.models import (
    Enrollment, Lead, LeadSource, LeadStatus, Payment,
)
from apps.leadreport import services


class LeadReportServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email='admin@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.branch = Branch.objects.create(name='Kathmandu', code='KTM')
        self.source = LeadSource.objects.create(name='Website')
        self.status = LeadStatus.objects.create(name='New')
        self.course = Course.objects.create(name='Python')

        self.lead = Lead.objects.create(
            full_name='Rahul Sharma',
            phone='9812345678',
            branch=self.branch,
            source=self.source,
            status=self.status,
            primary_course=self.course,
            expected_revenue=30000,
        )
        self.enrollment = Enrollment.objects.create(
            lead=self.lead,
            course=self.course,
            enrollment_date=timezone.now().date(),
            listed_fee=30000,
            agreed_amount=28000,
            status='confirmed',
            branch=self.branch,
        )
        Payment.objects.create(
            enrollment=self.enrollment,
            amount=14000,
            payment_date=timezone.now(),
            payment_method='cash',
            status='successful',
            branch=self.branch,
        )

    def _request(self, params=None):
        factory = APIRequestFactory()
        request = factory.get('/api/reports/overview/', params or {})
        request.user = self.user
        return request

    def test_overview(self):
        data = services.lead_overview_report(self._request())
        self.assertEqual(data['leads']['total'], 1)
        self.assertEqual(data['enrollments']['revenue_collected'], '14000.00')

    def test_leads_by_status(self):
        data = services.leads_by_status_report(self._request())
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['results'][0]['count'], 1)

    def test_leads_by_source(self):
        data = services.leads_by_source_report(self._request())
        self.assertEqual(data['results'][0]['source__name'], 'Website')

    def test_revenue(self):
        data = services.revenue_report(self._request())
        self.assertEqual(data['summary']['total_collected'], '14000.00')
        self.assertEqual(data['by_payment_method'][0]['payment_method'], 'cash')

    def test_revenue_outstanding(self):
        data = services.revenue_report(self._request())
        self.assertEqual(data['summary']['outstanding_to_collect'], '14000.00')

    def test_enrollments(self):
        data = services.enrollment_report(self._request())
        self.assertEqual(data['summary']['total_enrollments'], 1)
        self.assertEqual(data['summary']['collected'], '14000.00')

    def test_lead_trend(self):
        data = services.lead_trend_report(self._request({'group_by': 'day'}))
        self.assertEqual(data['results'][0]['leads'], 1)

    def test_conversion(self):
        data = services.lead_conversion_report(self._request())
        self.assertEqual(data['funnel']['total_leads'], 1)
        self.assertEqual(data['funnel']['enrolled'], 1)

    def test_invalid_date(self):
        data = services.lead_overview_report(self._request({'start_date': 'not-a-date'}))
        self.assertIn('error', data)

    def test_branch_scoping_for_non_staff(self):
        other_user = get_user_model().objects.create_user(
            email='c@test.com', name='Counselor', password='pass',
        )
        branch2 = Branch.objects.create(name='Pokhara', code='PKR')
        other_user.branch = branch2
        other_user.save()
        request = self._request()
        request.user = other_user
        data = services.lead_overview_report(request)
        self.assertEqual(data['leads']['total'], 0)


class LeadReportViewTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email='admin@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.client.force_authenticate(self.user)

    def _path(self, name):
        from django.urls import reverse
        return reverse(f'report-{name}')

    def test_requires_authentication(self):
        client = self.client_class(force=False)
        response = client.get(self._path('overview'))
        self.assertIn(response.status_code, (401, 403))

    def test_overview_endpoint(self):
        response = self.client.get(self._path('overview'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('leads', response.json())

    def test_all_report_endpoints_ok(self):
        endpoints = [
            'overview', 'leads-by-status', 'leads-by-source',
            'leads-by-branch', 'leads-by-campaign', 'leads-by-counselor',
            'lead-trend', 'revenue', 'enrollments', 'conversion',
            'counselor-performance',
        ]
        for name in endpoints:
            response = self.client.get(self._path(name))
            self.assertEqual(response.status_code, 200, msg=name)

    def test_reports_list(self):
        response = self.client.get(self._path('list'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('available_reports', response.json())

class OwedReportTests(APITestCase):
    """Who owes, and what is due in a month, from the due dates."""

    def setUp(self):
        from datetime import timedelta

        self.timedelta = timedelta
        self.user = get_user_model().objects.create_user(
            email='owed@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.client.force_authenticate(self.user)
        self.branch = Branch.objects.create(name='Kathmandu', code='KTM')
        self.other_branch = Branch.objects.create(name='Pokhara', code='PKR')
        self.course = Course.objects.create(name='Python', full_amount=30000)
        self.today = timezone.localdate()
        self.people = 0

    def _enrollment(self, parts, paid=0, status='confirmed', branch=None, counselor=None, batch=None):
        """parts: list of (days from today, amount)."""
        from apps.lead.models import Installment, PaymentPlan

        self.people += 1
        lead = Lead.objects.create(
            full_name=f'Student {self.people}', phone=f'98000020{self.people:02d}',
            assigned_counselor=counselor,
        )
        enrollment = Enrollment.objects.create(
            lead=lead, course=self.course, enrollment_date=self.today, listed_fee=30000,
            agreed_amount=sum(amount for _, amount in parts), status=status,
            branch=branch or self.branch, batch=batch,
        )
        plan = PaymentPlan.objects.create(enrollment=enrollment, reason='Plan', created_by=self.user)
        for days, amount in parts:
            Installment.objects.create(
                plan=plan, due_on=self.today + self.timedelta(days=days), amount=amount,
            )
        if paid:
            Payment.objects.create(
                enrollment=enrollment, amount=paid, payment_date=timezone.now(),
                payment_method='cash', status='successful',
            )
        return enrollment

    def _get(self, url, **params):
        response = self.client.get(url, params)
        self.assertEqual(response.status_code, 200)
        return response.data

    def test_who_owes_shows_owed_late_and_next_due(self):
        enrollment = self._enrollment([(-40, 10000), (-10, 10000), (20, 10000)], paid=5000)
        data = self._get('/api/reports/who-owes/')
        self.assertEqual(len(data['results']), 1)
        row = data['results'][0]
        self.assertEqual(row['enrollment'], enrollment.id)
        self.assertEqual(row['owed'], '25000.00')
        self.assertEqual(row['late'], '15000.00')
        self.assertEqual(row['days_late'], 40)
        self.assertEqual(row['next_due_on'], str(self.today + self.timedelta(days=20)))
        self.assertEqual(row['next_due_amount'], '10000.00')
        summary = data['summary']
        self.assertEqual(summary['students'], 1)
        self.assertEqual(summary['total_owed'], '25000.00')
        self.assertEqual(summary['not_late'], '10000.00')
        self.assertEqual(summary['days_1_30'], '10000.00')
        self.assertEqual(summary['days_31_60'], '5000.00')
        self.assertEqual(summary['over_90'], '0')

    def test_who_owes_leaves_out_paid_pending_and_cancelled(self):
        self._enrollment([(-5, 10000)], paid=10000)
        self._enrollment([(-5, 10000)], status='pending')
        self._enrollment([(-5, 10000)], status='cancelled')
        owing = self._enrollment([(-5, 10000)])
        data = self._get('/api/reports/who-owes/')
        self.assertEqual([r['enrollment'] for r in data['results']], [owing.id])

    def test_who_owes_sorts_by_days_late_and_filters_late_only(self):
        not_late = self._enrollment([(10, 10000)])
        a_bit_late = self._enrollment([(-3, 10000)])
        very_late = self._enrollment([(-100, 10000)])
        data = self._get('/api/reports/who-owes/')
        self.assertEqual(
            [r['enrollment'] for r in data['results']], [very_late.id, a_bit_late.id, not_late.id],
        )
        self.assertEqual(data['summary']['over_90'], '10000.00')
        data = self._get('/api/reports/who-owes/', late_only='true')
        self.assertNotIn(not_late.id, [r['enrollment'] for r in data['results']])

    def test_who_owes_by_counselor_and_by_batch(self):
        from apps.course.models import CourseBatch

        users = get_user_model().objects
        ram = users.create_user(email='ram@test.com', name='Ram', password='pass')
        sita = users.create_user(email='sita@test.com', name='Sita', password='pass')
        nov = CourseBatch.objects.create(name='Python Nov', course=self.course, capacity=20)
        dec = CourseBatch.objects.create(name='Python Dec', course=self.course, capacity=20)
        late = self._enrollment([(-40, 10000)], counselor=ram, batch=nov)
        self._enrollment([(10, 5000)], counselor=ram, batch=nov)
        self._enrollment([(-5, 8000)], counselor=sita, batch=dec)
        nobody = self._enrollment([(10, 3000)])

        data = self._get('/api/reports/who-owes/')
        row = next(r for r in data['results'] if r['enrollment'] == late.id)
        self.assertEqual(
            (row['counselor'], row['counselor_name'], row['batch'], row['batch_name']),
            (ram.id, 'Ram', nov.id, 'Python Nov'),
        )
        self.assertEqual(
            [(g['counselor_name'], g['students'], g['owed'], g['late'], g['days_late'])
             for g in data['by_counselor']],
            [('Ram', 2, '15000.00', '10000.00', 40), ('Sita', 1, '8000.00', '8000.00', 5),
             (None, 1, '3000.00', '0', 0)],
        )
        self.assertEqual(
            [(g['batch_name'], g['students'], g['owed']) for g in data['by_batch']],
            [('Python Nov', 2, '15000.00'), ('Python Dec', 1, '8000.00'), (None, 1, '3000.00')],
        )

        mine = self._get('/api/reports/who-owes/', counselor=ram.id)
        self.assertEqual(len(mine['results']), 2)
        self.assertEqual(mine['summary']['total_owed'], '15000.00')
        unassigned = self._get('/api/reports/who-owes/', counselor='none')
        self.assertEqual([r['enrollment'] for r in unassigned['results']], [nobody.id])
        in_batch = self._get('/api/reports/who-owes/', batch=dec.id)
        self.assertEqual([g['counselor_name'] for g in in_batch['by_counselor']], ['Sita'])

    def test_who_owes_stays_inside_a_branch(self):
        here = self._enrollment([(-5, 10000)])
        self._enrollment([(-5, 10000)], branch=self.other_branch)
        data = self._get('/api/reports/who-owes/', branch_id=self.branch.id)
        self.assertEqual([r['enrollment'] for r in data['results']], [here.id])

    def test_due_in_month_defaults_to_next_month_and_takes_off_what_is_paid(self):
        first = (self.today.replace(day=1) + self.timedelta(days=32)).replace(day=1)
        days_to = (first - self.today).days + 4  # the 5th of next month
        enrollment = self._enrollment([(days_to, 30000)], paid=10000)
        self._enrollment([(0, 10000)])  # due this month
        self._enrollment([(days_to + 40, 10000)])  # due the month after
        data = self._get('/api/reports/due-in-month/')
        self.assertEqual(data['month'], first.strftime('%Y-%m'))
        self.assertEqual(len(data['results']), 1)
        row = data['results'][0]
        self.assertEqual(row['enrollment'], enrollment.id)
        self.assertEqual(row['left'], '20000.00')
        self.assertEqual(data['summary']['total_due'], '20000.00')

    def test_due_in_month_takes_a_month_and_refuses_a_bad_one(self):
        self._enrollment([(0, 10000)])
        data = self._get('/api/reports/due-in-month/', month=self.today.strftime('%Y-%m'))
        self.assertEqual(data['summary']['parts'], 1)
        data = self._get('/api/reports/due-in-month/', month='2026-13')
        self.assertEqual(data['error'], 'Invalid month. Use YYYY-MM format.')


User = get_user_model()


class EnquiryFunnelTests(APITestCase):
    """Where enquiries die: reached per stage, lost per stage and reason."""

    URL = '/api/reports/enquiry-funnel/'

    def setUp(self):
        self.ktm = Branch.objects.create(name='Kathmandu', code='KTM')
        self.pkr = Branch.objects.create(name='Pokhara', code='PKR')
        self.admin = User.objects.create_user(
            email='funnel@test.com', name='Admin', password='pass', is_staff=True, branch=self.ktm,
        )
        self.client.force_authenticate(self.admin)
        self.python = Course.objects.create(name='Python', full_amount=30000)
        self.java = Course.objects.create(name='Java', full_amount=20000)
        self.facebook = LeadSource.objects.create(name='Facebook')
        self.people = 0

    def _enquiry(self, course, *, moves=(), lost=None, won=False, source=None, branch=None):
        from apps.lead import enquiries

        self.people += 1
        lead = Lead.objects.create(full_name=f'Person {self.people}', phone=f'98000060{self.people:02d}')
        enquiry = enquiries.add_enquiry(
            lead, course=course, user=self.admin, source=source, branch=branch or self.ktm,
        )
        for stage in moves:
            enquiries.move_stage(enquiry, stage, user=self.admin)
        if lost:
            enquiries.mark_lost(enquiry, reason=lost, user=self.admin)
        if won:
            enquiry.refresh_from_db()
            enquiries._change(enquiry, 'won', user=self.admin)
        enquiry.refresh_from_db()
        return enquiry

    def test_funnel(self):
        self._enquiry(self.python)                                         # open at New
        self._enquiry(self.python, lost='not_reachable')                   # died at New
        self._enquiry(self.python, moves=['contacted'], lost='too_expensive')   # died at Contacted
        self._enquiry(self.python, moves=['contacted', 'interested'], lost='too_expensive')
        self._enquiry(self.java, moves=['contacted', 'interested'], won=True, source=self.facebook)

        data = self.client.get(self.URL).data
        self.assertEqual(data['summary'], {
            'enquiries': 5, 'won': 1, 'lost': 3, 'open': 1, 'stale': 0,
            'stale_days': 14, 'win_rate': 20.0,
        })
        self.assertEqual(
            [(f['stage'], f['reached'], f['open'], f['lost']) for f in data['funnel']],
            [('new', 5, 1, 1), ('contacted', 3, 0, 1), ('interested', 2, 0, 1), ('won', 1, 0, 0)],
        )
        self.assertEqual(data['funnel'][1]['lost_rate'], 33.3)
        self.assertEqual(
            [(r['reason'], r['count']) for r in data['lost_by_reason']],
            [('too_expensive', 2), ('not_reachable', 1)],
        )
        self.assertEqual(
            [(r['name'], r['enquiries'], r['won'], r['lost'], r['open']) for r in data['by_course']],
            [('Python', 4, 0, 3, 1), ('Java', 1, 1, 0, 0)],
        )
        self.assertEqual(
            [(r['name'], r['enquiries']) for r in data['by_source']],
            [('No source', 4), ('Facebook', 1)],
        )

    def test_filters_and_stale(self):
        from apps.lead.models import LeadCourseInterest

        old = self._enquiry(self.python, moves=['contacted'])
        LeadCourseInterest.objects.filter(pk=old.pk).update(
            stage_changed_at=timezone.now() - timezone.timedelta(days=20),
        )
        self._enquiry(self.java)
        self._enquiry(self.java, branch=self.pkr)

        data = self.client.get(self.URL, {'course': self.python.id}).data
        self.assertEqual((data['summary']['enquiries'], data['summary']['stale']), (1, 1))
        data = self.client.get(self.URL, {'branch_id': self.pkr.id}).data
        self.assertEqual(data['summary']['enquiries'], 1)
        today = str(timezone.localdate())
        data = self.client.get(self.URL, {'start_date': today, 'end_date': today}).data
        self.assertEqual(data['summary']['enquiries'], 3)
        self.assertEqual(self.client.get(self.URL, {'start_date': 'x'}).data, {'error': services.DATE_PARSE_ERROR})

    def test_branch_user_sees_their_branch_only(self):
        self._enquiry(self.python)
        self._enquiry(self.python, branch=self.pkr)
        counselor = User.objects.create_user(
            email='pkr-funnel@test.com', name='Pokhara', password='pass', branch=self.pkr,
        )
        self.client.force_authenticate(counselor)
        data = self.client.get(self.URL, {'branch_id': self.ktm.id}).data
        self.assertEqual(data['summary']['enquiries'], 1)

