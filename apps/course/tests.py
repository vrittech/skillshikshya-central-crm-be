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
        body = {
            'name': 'Python Nov', 'course': self.course.id, 'batch': self.slot.id, 'capacity': 20,
            **extra,
        }
        body = {k: v for k, v in body.items() if v is not ...}
        return self.client.post('/api/course-batches/', body, format='json')

    def test_new_batch_is_upcoming_and_physical(self):
        response = self._create()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data['status'], 'upcoming')
        self.assertEqual(response.data['status_display'], 'Upcoming')
        self.assertEqual(response.data['mode'], 'physical')
        self.assertEqual((response.data['capacity'], response.data['seats_left']), (20, 20))
        self.assertIsNone(response.data['trainer'])

    def test_seats_are_required_and_at_least_one(self):
        for capacity, message in [
            (..., 'Enter the number of seats.'),
            (None, 'Enter the number of seats.'),
            (0, 'A batch needs at least 1 seat.'),
        ]:
            response = self._create(capacity=capacity)
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertEqual(response.data['capacity'], [message])
        self.assertFalse(CourseBatch.objects.exists())

    def test_seats_can_be_changed_but_not_cleared(self):
        batch_id = self._create().data['id']
        url = f'/api/course-batches/{batch_id}/'
        response = self.client.patch(url, {'capacity': 30}, format='json')
        self.assertEqual(response.data['capacity'], 30)
        response = self.client.patch(url, {'capacity': None}, format='json')
        self.assertEqual(response.data['capacity'], ['Enter the number of seats.'])

    def test_batch_shows_its_shift_times(self):
        Batch.objects.filter(pk=self.slot.pk).update(start_time='06:00', end_time='08:00')
        response = self._create()
        self.assertEqual(
            (response.data['batch_name'], response.data['batch_start_time'], response.data['batch_end_time']),
            ('Morning', '06:00', '08:00'),
        )

    def test_seats_mode_status_and_trainer_are_saved(self):
        response = self._create(capacity=25, mode='online', status='running', trainer='Ram')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        batch = CourseBatch.objects.get(pk=response.data['id'])
        self.assertEqual(
            (batch.capacity, batch.mode, batch.status, batch.trainer),
            (25, 'online', 'running', 'Ram'),
        )
        self.assertEqual(response.data['mode_display'], 'Online')

    def test_new_batch_takes_the_creators_branch(self):
        from apps.branch.models import Branch

        ktm = Branch.objects.create(name='Kathmandu', code='KTM')
        pkr = Branch.objects.create(name='Pokhara', code='PKR')
        self.admin.branch = ktm
        self.admin.save()
        response = self._create()
        self.assertEqual((response.data['branch'], response.data['branch_name']), (ktm.id, 'Kathmandu'))
        response = self._create(name='Python Pokhara', branch=pkr.id)
        self.assertEqual(response.data['branch_name'], 'Pokhara')

    def test_shifts_take_the_creators_branch_and_blank_ones_are_shared(self):
        from apps.branch.models import Branch

        ktm = Branch.objects.create(name='Kathmandu', code='KTM')
        pkr = Branch.objects.create(name='Pokhara', code='PKR')
        self.admin.branch = ktm
        self.admin.save()
        response = self.client.post(
            '/api/batches/', {'name': 'Evening', 'start_time': '17:00', 'end_time': '19:00'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data['branch'], ktm.id)
        Batch.objects.create(name='Pokhara day', branch=pkr)

        ktm_user = User.objects.create_user(
            email='ktm@test.com', name='Ktm', password='pass', branch=ktm,
        )
        self.client.force_authenticate(ktm_user)
        names = {b['name'] for b in self.client.get('/api/batches/').data['results']}
        self.assertEqual(names, {'Morning', 'Evening'})

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


class BatchTestBase(APITestCase):
    """Two batches of one course, and helpers to add students with money."""

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


class BatchMoneyTests(BatchTestBase):
    """A batch's fees, collected, owed, costs and profit count on the money batch."""

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

    def test_profit_report_filters_by_start_date(self):
        self._enrollment(paid=30000, seat=self.batch)
        self._enrollment(batch=self.other, paid=5000)
        today = timezone.localdate()
        url = '/api/reports/batch-profit/'

        data = self.client.get(url, {'start_date': (today + timedelta(days=1)).isoformat()}).data
        self.assertEqual([r['name'] for r in data['results']], ['Python Dec'])
        self.assertEqual(data['summary']['collected'], '5000.00')
        data = self.client.get(url, {'end_date': today.isoformat()}).data
        self.assertEqual([r['name'] for r in data['results']], ['Python Nov'])
        data = self.client.get(url, {'start_date': today.isoformat(), 'end_date': today.isoformat()}).data
        self.assertEqual(data['summary']['batches'], 1)

        self.assertIn('error', self.client.get(url, {'start_date': 'nope'}).data)
        later = {'start_date': (today + timedelta(days=5)).isoformat(), 'end_date': today.isoformat()}
        self.assertIn('error', self.client.get(url, later).data)


class CloseBatchTests(BatchTestBase):
    """Closing keeps the figures of that day; later money shows as after close."""

    def _post(self, action, reason='Course finished', batch=None, **extra):
        return self.client.post(
            f'/api/course-batches/{(batch or self.batch).id}/{action}/', {'reason': reason, **extra},
            format='json',
        )

    def test_close_keeps_figures_and_later_money_shows_after_close(self):
        from apps.course.services import add_cost
        from apps.lead.models import Payment

        student = self._enrollment(paid=10000, seat=self.batch)
        add_cost(self.batch, kind='trainer', amount=4000, spent_on=timezone.localdate(), user=self.admin)

        response = self._post('close', close_anyway=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'closed')
        at_close = response.data['at_close']
        self.assertEqual((at_close['collected'], at_close['costs'], at_close['profit']),
                         ('10000.00', '4000.00', '6000.00'))
        self.assertEqual(response.data['closings'][0]['reason'], 'Course finished')

        # Late money and a late bill still count on this batch.
        Payment.objects.create(
            enrollment=student, amount=5000, payment_date=timezone.now(),
            payment_method='cash', status='successful',
        )
        add_cost(self.batch, kind='venue', amount=1000, spent_on=timezone.localdate(), user=self.admin)
        data = self._money()
        self.assertEqual(data['at_close']['collected'], '10000.00')
        self.assertEqual(data['after_close']['collected'], '5000.00')
        self.assertEqual(data['after_close']['costs'], '1000.00')
        self.assertEqual(data['figures']['collected'], '15000.00')
        self.assertEqual(data['figures']['profit'], '10000.00')
        self.assertIsNotNone(data['closed_at'])

        # Closing doesn't change the students' enrollments.
        student.refresh_from_db()
        self.assertEqual((student.status, student.current_batch_id), ('confirmed', self.batch.id))

        report = self.client.get('/api/reports/batch-profit/').data
        nov = next(r for r in report['results'] if r['batch'] == self.batch.id)
        self.assertEqual(nov['at_close']['collected'], '10000.00')
        self.assertEqual(nov['collected'], '15000.00')

    def test_close_and_reopen_need_a_reason_and_the_right_status(self):
        self.assertEqual(self._post('close', reason=' ').status_code, 400)
        self.assertEqual(self._post('reopen').data['detail'], 'Only a closed batch can be reopened.')
        self.assertEqual(self._post('close').status_code, 200)
        self.assertEqual(self._post('close').data['detail'], 'This batch is already closed.')

        response = self._post('reopen', reason='Two students still need classes')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'running')
        self.assertIsNone(response.data['at_close'])
        self.assertEqual(
            [c['kind'] for c in response.data['closings']], ['reopen', 'close'],
        )
        # A batch that hasn't started goes back to upcoming.
        self._post('close', batch=self.other)
        self.assertEqual(self._post('reopen', batch=self.other).data['status'], 'upcoming')

    def test_closing_again_keeps_new_figures(self):
        from apps.lead.models import Payment

        student = self._enrollment(paid=1000, seat=self.batch)
        self._post('close', close_anyway=True)
        self._post('reopen')
        Payment.objects.create(
            enrollment=student, amount=2000, payment_date=timezone.now(),
            payment_method='cash', status='successful',
        )
        data = self._post('close', close_anyway=True).data
        self.assertEqual(data['at_close']['collected'], '3000.00')
        self.assertEqual(data['after_close']['collected'], '0.00')

    def test_status_cannot_be_set_to_closed_or_out_of_closed_by_edit(self):
        url = f'/api/course-batches/{self.batch.id}/'
        response = self.client.patch(url, {'status': 'closed'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Close batch', str(response.data['status']))
        self._post('close')
        response = self.client.patch(url, {'status': 'running'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Reopen batch', str(response.data['status']))
        # Other fields of a closed batch can still be edited.
        response = self.client.patch(url, {'trainer': 'Ram', 'status': 'closed'}, format='json')
        self.assertEqual(response.status_code, 200)

    def test_closing_needs_permission(self):
        clerk = User.objects.create_user(email='clerk2@test.com', name='Clerk', password='pass')
        self.client.force_authenticate(clerk)
        self.assertEqual(self._post('close').status_code, 403)

    def test_close_rows_cannot_be_changed_or_deleted(self):
        from django.core.exceptions import ValidationError

        self._post('close')
        row = self.batch.closings.get()
        row.reason = 'Other'
        with self.assertRaises(ValidationError):
            row.save()
        with self.assertRaises(ValidationError):
            row.delete()


class ClosingTestBase(BatchTestBase):
    """A batch that started ten days ago (Nepal time), and students with a Student record."""

    def setUp(self):
        from apps.course.closing import nepal_today

        super().setUp()
        self.today = nepal_today()
        self.batch.start_date = self.today - timedelta(days=10)
        self.batch.save(update_fields=['start_date'])
        self.url = f'/api/course-batches/{self.batch.id}/'

    def _student(self, **kwargs):
        from apps.student.models import Student

        enrollment = self._enrollment(seat=self.batch, **kwargs)
        enrollment.student = Student.objects.create(
            full_name=enrollment.lead.full_name, phone=enrollment.lead.phone, lead=enrollment.lead,
        )
        enrollment.save(update_fields=['student'])
        return enrollment

    def _mark(self, enrollment, day, status='present'):
        from apps.student.models import Attendance

        Attendance.objects.create(
            student=enrollment.student, course=self.course, date=day, status=status,
        )


class CloseChecklistTests(ClosingTestBase):
    """Before a batch closes: students accounted for, attendance done, Teams back, who owes."""

    def _check(self):
        response = self.client.get(f'{self.url}close-check/')
        self.assertEqual(response.status_code, 200)
        return response.data

    def test_checklist_lists_what_is_still_open(self):
        from apps.lead.models import TeamsAccount
        from apps.lead.teams import give_licence

        studying = self._student(paid=10000)
        done = self._student(status='completed', paid=30000)
        pending = self._enrollment(status='pending')
        day1, day2 = self.today - timedelta(days=2), self.today - timedelta(days=1)
        self._mark(studying, day1)
        self._mark(done, day1)
        self._mark(studying, day2, 'absent')
        account = TeamsAccount.objects.create(email='seat@x.com', created_by=self.admin)
        give_licence(done, teams_account=account, user=self.admin)

        data = self._check()
        self.assertFalse(data['ready'])
        self.assertEqual(
            [(r['enrollment'], r['status']) for r in data['students']],
            [(studying.id, 'confirmed'), (pending.id, 'pending')],
        )
        attendance = data['attendance']
        self.assertEqual((attendance['days'], attendance['last_day']), (2, day2.isoformat()))
        self.assertEqual(
            [(r['enrollment'], r['missing'], r['dates']) for r in attendance['missing']],
            [(done.id, 1, [day2.isoformat()])],
        )
        self.assertEqual([(r['enrollment'], r['account']) for r in data['teams']], [(done.id, 'seat@x.com')])
        self.assertEqual(data['teams'][0]['state'], 'safe_to_remove')
        self.assertEqual(data['owing']['count'], 1)
        self.assertEqual(data['owing']['total'], '20000.00')
        self.assertEqual(data['owing']['people'][0]['enrollment'], studying.id)

    def test_no_attendance_at_all_is_open(self):
        self._student(status='completed')
        attendance = self._check()['attendance']
        self.assertTrue(attendance['none_taken'])
        self.assertFalse(attendance['ready'])

    def test_close_is_refused_with_open_items_unless_close_anyway(self):
        studying = self._student()
        response = self.client.post(f'{self.url}close/', {'reason': 'Course over'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('close anyway', response.data['detail'])
        self.assertEqual(response.data['checklist']['students'][0]['enrollment'], studying.id)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, 'running')
        self.assertFalse(self.batch.closings.exists())

        response = self.client.post(
            f'{self.url}close/', {'reason': 'Course over', 'close_anyway': True}, format='json',
        )
        self.assertEqual(response.status_code, 200)
        saved = response.data['closings'][0]['checklist']
        self.assertTrue(saved['closed_anyway'])
        self.assertEqual(saved['students'], [studying.lead.full_name])
        self.assertTrue(saved['attendance_none_taken'])

    def test_clear_checklist_closes_even_when_someone_owes(self):
        done = self._student(status='completed', paid=5000)
        self._mark(done, self.today)
        check = self._check()
        self.assertTrue(check['ready'])
        self.assertEqual(check['owing']['count'], 1)

        response = self.client.post(f'{self.url}close/', {'reason': 'Course over'}, format='json')
        self.assertEqual(response.status_code, 200)
        saved = response.data['closings'][0]['checklist']
        self.assertEqual((saved['ready'], saved['closed_anyway']), (True, False))
        self.assertEqual((saved['owing_count'], saved['owing_total']), (1, '25000.00'))

    def test_mark_completed_finishes_students_with_a_history_row(self):
        from apps.lead.models import EnrollmentHistory

        first = self._student()
        second = self._student(status='active')
        pending = self._enrollment(status='pending')
        url = f'{self.url}complete-students/'

        response = self.client.post(url, {'reason': 'Course finished', 'enrollments': [first.id]}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['completed'], [first.id])
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.status, second.status), ('completed', 'active'))
        row = EnrollmentHistory.objects.get(enrollment=first, action='complete')
        self.assertEqual((row.reason, row.before, row.after),
                         ('Course finished', {'status': 'confirmed'}, {'status': 'completed'}))

        response = self.client.post(url, {'reason': 'Course finished'}, format='json')
        self.assertEqual(response.data['completed'], [second.id])
        self.assertEqual([r['enrollment'] for r in response.data['checklist']['students']], [pending.id])
        pending.refresh_from_db()
        self.assertEqual(pending.status, 'pending')

        self.assertEqual(self.client.post(url, {'reason': 'Again'}, format='json').status_code, 400)
        self.assertEqual(self.client.post(url, {'reason': ' '}, format='json').status_code, 400)

    def test_mark_completed_needs_permission_and_an_open_batch(self):
        self._student()
        clerk = User.objects.create_user(email='clerk3@test.com', name='Clerk', password='pass')
        self.client.force_authenticate(clerk)
        url = f'{self.url}complete-students/'
        self.assertEqual(self.client.post(url, {'reason': 'Done'}, format='json').status_code, 403)
        self.assertEqual(self.client.get(f'{self.url}close-check/').status_code, 200)

        self.client.force_authenticate(self.admin)
        self.client.post(f'{self.url}close/', {'reason': 'Over', 'close_anyway': True}, format='json')
        response = self.client.post(url, {'reason': 'Done'}, format='json')
        self.assertEqual(response.data['detail'], 'This batch is closed. Reopen it first.')


class BatchAttendanceTests(ClosingTestBase):
    """The batch's register: one day, everyone at once, with each student's totals."""

    def _register(self, day=None):
        response = self.client.get(f'{self.url}attendance/', {'date': day.isoformat()} if day else {})
        self.assertEqual(response.status_code, 200)
        return response.data

    def _save(self, day, marks):
        return self.client.post(
            f'{self.url}attendance/',
            {'date': day.isoformat(), 'marks': [{'student': e.student_id, 'status': s} for e, s in marks]},
            format='json',
        )

    def test_mark_a_day_and_count_totals(self):
        ram, sita = self._student(), self._student()
        yesterday = self.today - timedelta(days=1)

        response = self._save(self.today, [(ram, 'present'), (sita, 'absent')])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([r['status'] for r in response.data['students']], ['present', 'absent'])
        self._save(yesterday, [(ram, 'present'), (sita, 'present')])

        today = self._register()
        self.assertEqual(today['date'], self.today.isoformat())
        self.assertEqual(today['days'], [self.today.isoformat(), yesterday.isoformat()])
        rows = {r['enrollment']: r for r in today['students']}
        self.assertEqual((rows[ram.id]['present'], rows[ram.id]['absent'], rows[ram.id]['total']), (2, 0, 2))
        self.assertEqual((rows[sita.id]['present'], rows[sita.id]['absent']), (1, 1))
        earlier = self._register(self.today - timedelta(days=3))
        self.assertEqual([r['status'] for r in earlier['students']], [None, None])

        # Saving the same day again changes the mark, never adds a second one.
        self._save(self.today, [(sita, 'present')])
        rows = {r['enrollment']: r for r in self._register()['students']}
        self.assertEqual((rows[sita.id]['status'], rows[sita.id]['total']), ('present', 2))

    def test_refuses_future_days_other_students_and_a_closed_batch(self):
        from apps.course.models import CourseBatch

        ram = self._student()
        self.assertEqual(self._save(self.today + timedelta(days=1), [(ram, 'present')]).status_code, 400)
        self.assertEqual(
            self._save(self.today - timedelta(days=11), [(ram, 'present')]).data['detail'],
            'This day is before the batch starts.',
        )
        elsewhere = self._student(status='confirmed')
        elsewhere.current_batch = self.other
        elsewhere.save(update_fields=['current_batch'])
        self.assertEqual(self._save(self.today, [(elsewhere, 'present')]).status_code, 400)

        CourseBatch.objects.filter(pk=self.batch.pk).update(status='closed')
        self.assertEqual(self._save(self.today, [(ram, 'present')]).status_code, 400)
        self.assertTrue(self._register()['closed'])

    def test_changing_a_saved_mark_needs_change_permission(self):
        from django.contrib.auth.models import Permission

        ram, sita = self._student(), self._student()
        clerk = User.objects.create_user(email='clerk4@test.com', name='Clerk', password='pass')
        self.client.force_authenticate(clerk)
        self.assertEqual(self._register()['students'][0]['status'], None)
        self.assertEqual(self._save(self.today, [(ram, 'present')]).status_code, 403)

        clerk.user_permissions.add(Permission.objects.get(codename='add_attendance'))
        clerk = User.objects.get(pk=clerk.pk)
        self.client.force_authenticate(clerk)
        self.assertEqual(self._save(self.today, [(ram, 'present')]).status_code, 200)
        # Same mark again plus a new one is fine; changing a saved mark is not.
        self.assertEqual(self._save(self.today, [(ram, 'present'), (sita, 'absent')]).status_code, 200)
        self.assertEqual(self._save(self.today, [(ram, 'absent')]).status_code, 400)

        clerk.user_permissions.add(Permission.objects.get(codename='change_attendance'))
        self.client.force_authenticate(User.objects.get(pk=clerk.pk))
        self.assertEqual(self._save(self.today, [(ram, 'absent')]).status_code, 200)


class ShiftTests(APITestCase):
    """A shift has a start and end time on the same day, and a duration."""

    def setUp(self):
        self.admin = User.objects.create_user(
            email='slots@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.client.force_authenticate(self.admin)

    def _create(self, expect=201, **body):
        data = {'name': 'Morning', 'start_time': '06:00', 'end_time': '08:00', **body}
        response = self.client.post('/api/batches/', data, format='json')
        self.assertEqual(response.status_code, expect, response.data)
        return response.data

    def test_times_and_duration(self):
        data = self._create(start_time='06:30', end_time='08:00')
        self.assertEqual(
            (data['start_time'], data['end_time'], data['duration_minutes']), ('06:30', '08:00', 90),
        )

    def test_end_must_be_after_start_and_at_least_15_minutes(self):
        for start, end in [('08:00', '06:00'), ('08:00', '08:00')]:
            refused = self._create(expect=400, start_time=start, end_time=end)
            self.assertEqual(str(refused['end_time'][0]), 'The end time must be after the start time.')
        refused = self._create(expect=400, start_time='08:00', end_time='08:10')
        self.assertEqual(str(refused['end_time'][0]), 'A shift must be at least 15 minutes.')
        refused = self._create(expect=400, start_time=None, end_time=None)
        self.assertIn('start_time', refused)
        self.assertFalse(Batch.objects.exists())

    def test_name_is_unique_in_a_branch(self):
        from apps.branch.models import Branch

        self._create()
        refused = self._create(expect=400, name=' morning ')
        self.assertEqual(str(refused['name'][0]), 'A shift named morning already exists.')
        self._create(name='Morning', branch=Branch.objects.create(name='Pokhara', code='PKR').id)

    def test_editing_checks_the_times_it_keeps(self):
        slot = self._create()
        url = f'/api/batches/{slot["id"]}/'
        refused = self.client.patch(url, {'end_time': '05:00'}, format='json')
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(self.client.patch(url, {'end_time': '09:00'}, format='json').data['duration_minutes'], 180)
        old = Batch.objects.create(name='Old evening')
        response = self.client.patch(f'/api/batches/{old.id}/', {'is_active': False}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data['duration_minutes'])

    def test_list_is_morning_first_and_no_time_last(self):
        self._create(name='Evening', start_time='17:00', end_time='19:00')
        Batch.objects.create(name='Old')
        self._create(name='Day', start_time='12:00', end_time='14:00')
        self._create(name='Early', start_time='06:00', end_time='08:00')
        names = [s['name'] for s in self.client.get('/api/batches/').data['results']]
        self.assertEqual(names, ['Early', 'Day', 'Evening', 'Old'])

    def test_old_typed_times_are_read_into_start_and_end(self):
        from importlib import import_module

        from django.apps import apps as django_apps

        fill = import_module('apps.course.migrations.0009_fill_time_slot_times').fill_times
        a = Batch.objects.create(name='A', description='6:00 AM – 8:00 AM')
        b = Batch.objects.create(name='B', description='5:30pm-7 PM')
        c = Batch.objects.create(name='C', description='Weekends')
        fill(django_apps, None)
        for slot in (a, b, c):
            slot.refresh_from_db()
        self.assertEqual((str(a.start_time), str(a.end_time)), ('06:00:00', '08:00:00'))
        self.assertEqual((str(b.start_time), str(b.end_time)), ('17:30:00', '19:00:00'))
        self.assertIsNone(c.start_time)
