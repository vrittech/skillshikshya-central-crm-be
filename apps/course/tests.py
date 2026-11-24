from datetime import timedelta
from importlib import import_module

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.course.models import Batch, Course, CourseBatch

User = get_user_model()


class CourseBatchDetailsTests(APITestCase):
    """A batch has seats, a status, a mode (online or physical) and a trainer."""

    def setUp(self):
        self.admin = User.objects.create_user(
            email='admin@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.course = Course.objects.create(name='Python', full_amount=30000)
        self.slot = Batch.objects.create(name='Morning')

    def _create(self, **extra):
        body = {'name': 'Python Nov', 'course': self.course.id, 'batch': self.slot.id, **extra}
        return self.client.post('/api/course-batches/', body, format='json')

    def test_new_batch_is_upcoming_physical_with_no_seat_limit(self):
        response = self._create()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data['status'], 'upcoming')
        self.assertEqual(response.data['status_display'], 'Upcoming')
        self.assertEqual(response.data['mode'], 'physical')
        self.assertIsNone(response.data['capacity'])
        self.assertIsNone(response.data['trainer'])

    def test_seats_mode_status_and_trainer_are_saved(self):
        response = self._create(capacity=25, mode='online', status='running', trainer='Ram')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        batch = CourseBatch.objects.get(pk=response.data['id'])
        self.assertEqual(
            (batch.capacity, batch.mode, batch.status, batch.trainer),
            (25, 'online', 'running', 'Ram'),
        )
        self.assertEqual(response.data['mode_display'], 'Online')

    def test_unknown_status_is_refused(self):
        response = self._create(status='finished')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('status', response.data)

    def test_negative_seats_are_refused(self):
        response = self._create(capacity=-1)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('capacity', response.data)

    def test_list_filters_by_status_and_mode(self):
        CourseBatch.objects.create(name='A', course=self.course, status='running', mode='online')
        CourseBatch.objects.create(name='B', course=self.course, status='closed')
        CourseBatch.objects.create(name='C', course=self.course, status='upcoming')

        response = self.client.get('/api/course-batches/?status=running&status=upcoming')
        self.assertEqual(sorted(b['name'] for b in response.data['results']), ['A', 'C'])

        response = self.client.get('/api/course-batches/?mode=online')
        self.assertEqual([b['name'] for b in response.data['results']], ['A'])


class MarkStartedBatchesRunningTests(APITestCase):
    """The migration marks batches that already started as running, and closes none."""

    def test_started_batches_become_running(self):
        course = Course.objects.create(name='Python', full_amount=30000)
        today = timezone.localdate()
        started = CourseBatch.objects.create(
            name='Started', course=course, start_date=today - timedelta(days=10),
        )
        later = CourseBatch.objects.create(
            name='Later', course=course, start_date=today + timedelta(days=10),
        )
        no_date = CourseBatch.objects.create(name='No date', course=course)

        migration = import_module('apps.course.migrations.0005_batch_seats_status_mode_trainer')
        migration.mark_started_batches_running(django_apps, None)

        for batch, expected in ((started, 'running'), (later, 'upcoming'), (no_date, 'upcoming')):
            batch.refresh_from_db()
            self.assertEqual(batch.status, expected, batch.name)


class BatchMoneyTests(APITestCase):
    """A batch's fees, collected, owed, costs and profit count on the money batch."""

    def setUp(self):
        from apps.branch.models import Branch

        self.admin = User.objects.create_user(
            email='money@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.branch = Branch.objects.create(name='Kathmandu', code='KTM')
        self.course = Course.objects.create(name='Python', full_amount=30000)
        self.batch = CourseBatch.objects.create(
            name='Python Nov', course=self.course, status='running', branch=self.branch,
            start_date=timezone.localdate(),
        )
        self.other = CourseBatch.objects.create(
            name='Python Dec', course=self.course, status='upcoming', branch=self.branch,
            start_date=timezone.localdate() + timedelta(days=30),
        )
        self.people = 0

    def _enrollment(self, *, batch=None, agreed=30000, paid=0, status='confirmed', seat=None):
        from apps.lead.models import Enrollment, Lead, Payment

        self.people += 1
        lead = Lead.objects.create(full_name=f'Student {self.people}', phone=f'98000030{self.people:02d}')
        enrollment = Enrollment.objects.create(
            lead=lead, course=self.course, enrollment_date=timezone.localdate(),
            listed_fee=agreed, agreed_amount=agreed, status=status, branch=self.branch,
            batch=batch or self.batch, current_batch=seat,
        )
        if paid:
            Payment.objects.create(
                enrollment=enrollment, amount=paid, payment_date=timezone.now(),
                payment_method='cash', status='successful',
            )
        return enrollment

    def _adjust(self, enrollment, kind, amount):
        from apps.lead.models import Adjustment

        Adjustment.objects.create(
            enrollment=enrollment, kind=kind, amount=amount, reason='Test', created_by=self.admin,
        )

    def _money(self, batch=None):
        response = self.client.get(f'/api/course-batches/{(batch or self.batch).id}/money/')
        self.assertEqual(response.status_code, 200)
        return response.data

    def test_figures_count_on_the_money_batch(self):
        from apps.lead.models import Payment

        a = self._enrollment(paid=10000, seat=self.batch)
        self._adjust(a, 'extra_charge', 2000)
        self._adjust(a, 'discount', 1000)
        b = self._enrollment(agreed=20000, paid=20000, seat=self.batch)
        self._adjust(b, 'refund', 5000)
        # Moved in from another batch: its money counts there, not here.
        self._enrollment(batch=self.other, paid=30000, seat=self.batch)
        # Cancelled, but the money paid was kept.
        self._enrollment(paid=3000, status='cancelled')
        # Pending: not a student yet.
        self._enrollment(status='pending')
        # A voided payment never counts.
        Payment.objects.create(
            enrollment=a, amount=999, payment_date=timezone.now(),
            payment_method='cash', status='voided',
        )

        figures = self._money()['figures']
        self.assertEqual(figures['students'], 2)
        self.assertEqual(figures['fees'], '51000.00')  # 31000 + 20000
        self.assertEqual(figures['collected'], '28000.00')  # 10000 + 15000 + 3000
        self.assertEqual(figures['owed'], '26000.00')  # 21000 + 5000
        self.assertEqual(figures['costs'], '0')
        self.assertEqual(figures['profit'], '28000.00')

        other = self._money(self.other)['figures']
        self.assertEqual(other['collected'], '30000.00')

    def test_add_and_void_cost(self):
        self._enrollment(paid=30000)
        response = self.client.post(
            f'/api/course-batches/{self.batch.id}/costs/',
            {'kind': 'trainer', 'amount': '12000', 'note': 'November classes'}, format='json',
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['figures']['costs'], '12000.00')
        self.assertEqual(response.data['figures']['profit'], '18000.00')
        cost = response.data['costs'][0]
        self.assertEqual(cost['kind_display'], 'Trainer')
        self.assertEqual(cost['spent_on'], timezone.localdate().isoformat())
        self.assertEqual(cost['created_by_name'], 'Admin')

        url = f'/api/course-batches/{self.batch.id}/costs/{cost["id"]}/void/'
        self.assertEqual(self.client.post(url, {'reason': ' '}, format='json').status_code, 400)
        response = self.client.post(url, {'reason': 'Typed the wrong amount'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['figures']['costs'], '0')
        self.assertEqual(response.data['costs'][0]['void_reason'], 'Typed the wrong amount')
        self.assertEqual(response.data['costs'][0]['voided_by_name'], 'Admin')

        response = self.client.post(url, {'reason': 'Again'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['detail'], 'This cost is already voided.')

    def test_cost_needs_a_positive_amount_and_a_kind(self):
        url = f'/api/course-batches/{self.batch.id}/costs/'
        self.assertEqual(self.client.post(url, {'kind': 'trainer', 'amount': '0'}).status_code, 400)
        self.assertEqual(self.client.post(url, {'kind': 'food', 'amount': '10'}).status_code, 400)

    def test_cost_of_another_batch_cannot_be_voided_here(self):
        from apps.course.services import add_cost

        cost = add_cost(self.other, kind='venue', amount=500, spent_on=timezone.localdate(), user=self.admin)
        response = self.client.post(
            f'/api/course-batches/{self.batch.id}/costs/{cost.id}/void/', {'reason': 'x'},
        )
        self.assertEqual(response.status_code, 404)

    def test_costs_cannot_be_edited_or_deleted(self):
        from django.core.exceptions import ValidationError

        from apps.course.services import add_cost

        cost = add_cost(self.batch, kind='venue', amount=500, spent_on=timezone.localdate(), user=self.admin)
        cost.amount = 50
        with self.assertRaises(ValidationError):
            cost.save()
        with self.assertRaises(ValidationError):
            cost.delete()

    def test_adding_a_cost_needs_permission(self):
        from django.contrib.auth.models import Permission

        clerk = User.objects.create_user(email='clerk@test.com', name='Clerk', password='pass')
        self.client.force_authenticate(clerk)
        url = f'/api/course-batches/{self.batch.id}/costs/'
        body = {'kind': 'trainer', 'amount': '100'}
        self.assertEqual(self.client.post(url, body).status_code, 403)
        clerk.user_permissions.add(Permission.objects.get(codename='add_batchcost'))
        clerk = User.objects.get(pk=clerk.pk)
        self.client.force_authenticate(clerk)
        self.assertEqual(self.client.post(url, body).status_code, 201)

    def test_profit_report_lists_batches_with_totals(self):
        from apps.course.services import add_cost

        self._enrollment(paid=30000, seat=self.batch)
        self._enrollment(batch=self.other, paid=5000)
        add_cost(self.batch, kind='trainer', amount=10000, spent_on=timezone.localdate(), user=self.admin)

        data = self.client.get('/api/reports/batch-profit/').data
        self.assertEqual(data['summary']['batches'], 2)
        self.assertEqual(data['summary']['collected'], '35000.00')
        self.assertEqual(data['summary']['costs'], '10000.00')
        self.assertEqual(data['summary']['profit'], '25000.00')
        # Newest start date first.
        self.assertEqual([r['name'] for r in data['results']], ['Python Dec', 'Python Nov'])
        nov = data['results'][1]
        self.assertEqual((nov['collected'], nov['costs'], nov['profit']), ('30000.00', '10000.00', '20000.00'))

        data = self.client.get('/api/reports/batch-profit/', {'status': 'running'}).data
        self.assertEqual([r['name'] for r in data['results']], ['Python Nov'])
        self.assertIn('error', self.client.get('/api/reports/batch-profit/', {'status': 'x'}).data)
