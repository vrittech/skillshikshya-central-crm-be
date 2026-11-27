"""Batches and enrollments end to end, through the API, the way staff use them.

Each test drives real URLs across features: seats, moves, pauses, close and
reopen, batch money, the register, Teams licences and the default roles.
Single rules have their own tests in apps/course/tests.py and
apps/lead/tests.py; these tests cross them.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.branch.models import Branch
from apps.course.closing import nepal_today
from apps.course.models import Batch, Course, CourseBatch
from apps.lead.models import Enrollment, Lead, TeamsAccount, TeamsLicence
from apps.student.models import Attendance

User = get_user_model()


class FlowBase(APITestCase):
    """Two branches, a Python and a Java course, and helpers that go through the API."""

    def setUp(self):
        self.today = nepal_today()
        self.ktm = Branch.objects.create(name='Kathmandu', code='FLKTM')
        self.pkr = Branch.objects.create(name='Pokhara', code='FLPKR')
        self.admin = User.objects.create_user(
            email='flow-admin@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.python = Course.objects.create(name='Python', full_amount=30000)
        self.java = Course.objects.create(name='Java', full_amount=20000)
        self.people = 0
        self.users = 0

    # ------------------------------------------------------------ people

    def _user(self, role, branch=None):
        """A user with one of the default roles (apps/account/migrations/0010_default_roles.py)."""
        self.users += 1
        user = User.objects.create_user(
            email=f'flow-user{self.users}@test.com', name=f'{role} {self.users}', password='pass',
            branch=branch or self.ktm,
        )
        user.groups.add(Group.objects.get(name=role))
        return User.objects.get(pk=user.pk)

    def _as(self, user):
        self.client.force_authenticate(user)

    def _lead(self, branch=None):
        self.people += 1
        return Lead.objects.create(
            full_name=f'Student {self.people:02d}', phone=f'98111{self.people:05d}',
            email=f'student{self.people}@test.com', branch=branch or self.ktm,
        )

    # ------------------------------------------------------------ batches

    def _batch(self, name, *, seats=2, course=None, branch=None, status='running', started=10, ends=None):
        body = {
            'name': name, 'course': (course or self.python).id, 'capacity': seats, 'status': status,
            'branch': (branch or self.ktm).id, 'batch': None,
            'start_date': (self.today - timedelta(days=started)).isoformat(),
        }
        if ends is not None:
            body['end_date'] = (self.today + timedelta(days=ends)).isoformat()
        response = self.client.post('/api/course-batches/', body, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return CourseBatch.objects.get(pk=response.data['id'])

    def _batch_url(self, batch, action=''):
        return f'/api/course-batches/{batch.id}/{action}'

    def _seats(self, batch):
        data = self.client.get(self._batch_url(batch)).data
        return data['seats_taken'], data['seats_left'], data['waiting_count']

    def _roster(self, batch):
        response = self.client.get(self._batch_url(batch, 'students/'))
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def _check(self, batch):
        response = self.client.get(self._batch_url(batch, 'close-check/'))
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def _close(self, batch, reason='Course over', **extra):
        return self.client.post(self._batch_url(batch, 'close/'), {'reason': reason, **extra}, format='json')

    def _reopen(self, batch, reason='Two classes left'):
        return self.client.post(self._batch_url(batch, 'reopen/'), {'reason': reason}, format='json')

    def _money(self, batch):
        response = self.client.get(self._batch_url(batch, 'money/'))
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def _add_cost(self, batch, kind, amount):
        return self.client.post(
            self._batch_url(batch, 'costs/'), {'kind': kind, 'amount': amount}, format='json',
        )

    def _mark(self, batch, day, marks):
        """Save the register for a day. marks: [(enrollment, 'present' | 'absent')]."""
        return self.client.post(self._batch_url(batch, 'attendance/'), {
            'date': day.isoformat(),
            'marks': [{'student': e.student_id, 'status': s} for e, s in marks],
        }, format='json')

    # ------------------------------------------------------------ enrollments

    def _url(self, enrollment, action=''):
        return f'/api/leads/{enrollment.lead_id}/enrollments/{enrollment.id}/{action}'

    def _enroll_response(self, batch=None, *, lead=None, course=None):
        lead = lead or self._lead()
        course = course or (batch.course if batch is not None else self.python)
        body = {
            'course': course.id, 'enrollment_date': self.today.isoformat(),
            'listed_fee': str(course.full_amount), 'agreed_amount': str(course.full_amount),
        }
        if batch is not None:
            body['batch'] = batch.id
        return self.client.post(f'/api/leads/{lead.id}/enrollments/', body, format='json')

    def _enroll(self, batch=None, **kwargs):
        """A pending enrollment, added through the API."""
        response = self._enroll_response(batch, **kwargs)
        self.assertEqual(response.status_code, 201, response.data)
        return Enrollment.objects.get(pk=response.data['id'])

    def _confirm(self, enrollment, paid='5000'):
        return self.client.post(
            self._url(enrollment, 'confirm/'),
            {'first_payment': {'amount': paid, 'payment_method': 'cash'}}, format='json',
        )

    def _student(self, batch=None, *, paid='5000', **kwargs):
        """Enroll and confirm: a student sitting in the batch (or in none without a batch)."""
        enrollment = self._enroll(batch, **kwargs)
        response = self._confirm(enrollment, paid)
        self.assertEqual(response.status_code, 200, response.data)
        enrollment.refresh_from_db()
        return enrollment

    def _act(self, enrollment, action, **body):
        return self.client.post(self._url(enrollment, f'{action}/'), body, format='json')

    def _detail(self, enrollment):
        return self.client.get(self._url(enrollment)).data

    def _ok(self, response, code=200):
        self.assertEqual(response.status_code, code, getattr(response, 'data', None))
        return response.data

    def _refused(self, response, text):
        self.assertEqual(response.status_code, 400, getattr(response, 'data', None))
        self.assertIn(text, str(response.data))

    # ------------------------------------------------------------ Teams

    def _account(self, email, branch=None):
        body = {'emails': [email]}
        if branch is not None:
            body['branch'] = branch.id
        self._ok(self.client.post('/api/teams-accounts/', body, format='json'), 201)
        return TeamsAccount.objects.get(email=email)

    def _give(self, enrollment, account):
        return self.client.post(
            '/api/teams-licences/', {'enrollment': enrollment.id, 'teams_account': account.id}, format='json',
        )

    def _licence(self, enrollment):
        return TeamsLicence.objects.get(enrollment=enrollment, released_at__isnull=True)

    def _licence_state(self, licence):
        state = self.client.get(f'/api/teams-licences/{licence.id}/').data['state']
        return state['state'], state['reason']

    def _free(self, licence, reason='Course over'):
        return self.client.post(f'/api/teams-licences/{licence.id}/free/', {'reason': reason}, format='json')

    def _summary(self):
        data = self.client.get('/api/teams-accounts/summary/').data
        return {k: data[k] for k in ('total', 'ready', 'in_use', 'safe_to_remove', 'action_required', 'retired')}


def money(value):
    return Decimal(value)


# ==================================================================== seats


class SeatCountTests(FlowBase):
    """Seats decide who can join, and the batch list counts them right after every change."""

    def _counts_are(self, batch, taken, left, waiting):
        listed = self.client.get('/api/course-batches/', {'course': batch.course_id}).data['results']
        row = next(r for r in listed if r['id'] == batch.id)
        self.assertEqual((row['seats_taken'], row['seats_left'], row['waiting_count']), (taken, left, waiting))
        self.assertEqual(self._seats(batch), (taken, left, waiting))
        self.assertEqual(len(self._roster(batch)['seated']), taken)
        self.assertEqual(
            Enrollment.objects.filter(current_batch=batch, status__in=Enrollment.SEAT_STATUSES).count(), taken,
        )

    def test_counts_on_the_list_match_the_students_after_every_change(self):
        nov = self._batch('Python Nov', seats=2)
        dec = self._batch('Python Dec', seats=2)
        ram, sita, hari = self._enroll(nov), self._enroll(nov), self._enroll(nov)
        self._counts_are(nov, 0, 2, 3)

        self._ok(self._confirm(ram))
        self._ok(self._confirm(sita))
        self._counts_are(nov, 2, 0, 1)
        self._refused(self._confirm(hari), 'Python Nov is full')
        self._counts_are(nov, 2, 0, 1)

        self._ok(self._act(ram, 'move', batch=dec.id, reason='Evening suits him'))
        self._counts_are(nov, 1, 1, 1)
        self._counts_are(dec, 1, 1, 0)

        self._ok(self._act(sita, 'pause', reason='Exams'))
        self._counts_are(nov, 0, 2, 1)
        self._ok(self._confirm(hari))
        self._counts_are(nov, 1, 1, 0)
        self._ok(self._act(sita, 'resume', batch=nov.id, reason='Exams over'))
        self._counts_are(nov, 2, 0, 0)

        self._ok(self._act(hari, 'cancel', reason='Moved abroad'))
        self._counts_are(nov, 1, 1, 0)
        self._ok(self._act(sita, 'status', status='dropped_out', reason='Stopped coming'))
        self._counts_are(nov, 0, 2, 0)
        self._counts_are(dec, 1, 1, 0)

    def test_the_last_seat_goes_to_the_first_confirm_and_comes_back_on_cancel(self):
        nov = self._batch('Python Nov', seats=1)
        first, second = self._enroll(nov), self._enroll(nov)
        self._ok(self._confirm(second))

        self._refused(self._confirm(first), 'Python Nov is full (1 of 1 seats)')
        first.refresh_from_db()
        self.assertEqual((first.status, first.student_id, first.payments.count()), ('pending', None, 0))
        self.assertFalse(Lead.objects.get(pk=first.lead_id).is_converted)

        self._ok(self._act(second, 'cancel', reason='Found a job'))
        data = self._ok(self._confirm(first))
        self.assertEqual(data['current_batch'], nov.id)
        self.assertEqual(self._seats(nov), (1, 0, 0))

    def test_seats_cut_to_the_seats_taken_stop_the_waiting_student(self):
        nov = self._batch('Python Nov', seats=3)
        self._student(nov)
        self._student(nov)
        waiting = self._enroll(nov)
        url = self._batch_url(nov)

        self._refused(self.client.patch(url, {'capacity': 1}, format='json'), '2 students sit in this batch now')
        self._ok(self.client.patch(url, {'capacity': 2}, format='json'))
        self.assertEqual(self._seats(nov), (2, 0, 1))
        self._refused(self._confirm(waiting), 'Python Nov is full (2 of 2 seats)')

        self._ok(self.client.patch(url, {'capacity': 3}, format='json'))
        self._ok(self._confirm(waiting))
        self.assertEqual(self._seats(nov), (3, 0, 0))

    def test_a_full_batch_refuses_join(self):
        nov = self._batch('Python Nov', seats=1)
        dec = self._batch('Python Dec', seats=1)
        self._student(nov)
        later = self._student()  # confirmed without a batch
        self.assertEqual(self._detail(later)['seat_state'], 'none')

        self._refused(self._act(later, 'join-batch', batch=nov.id), 'Python Nov is full')
        self.assertEqual(self._detail(later)['seat_state'], 'none')
        data = self._ok(self._act(later, 'join-batch', batch=dec.id))
        self.assertEqual((data['current_batch'], data['batch']), (dec.id, dec.id))

    def test_a_reinstated_student_finds_the_seat_taken(self):
        nov = self._batch('Python Nov', seats=1)
        dec = self._batch('Python Dec', seats=1)
        ram = self._student(nov)
        self._ok(self._act(ram, 'cancel', reason='Family moved'))
        self._student(nov)  # takes the freed seat

        self.assertEqual(self._ok(self._act(ram, 'reinstate', reason='Came back'))['status'], 'confirmed')
        detail = self._detail(ram)
        self.assertEqual((detail['seat_state'], detail['current_batch']), ('none', None))
        self._refused(self._act(ram, 'join-batch', batch=nov.id), 'Python Nov is full')
        data = self._ok(self._act(ram, 'join-batch', batch=dec.id, reason='Back for December'))
        # The money stays on the batch he first joined.
        self.assertEqual((data['current_batch'], data['batch']), (dec.id, nov.id))

    def test_seat_actions_refuse_a_batch_of_another_branch(self):
        nov = self._batch('Python Nov')
        pokhara = self._batch('Python Pokhara', branch=self.pkr)
        ram = self._student(nov)
        self._refused(self._act(ram, 'move', batch=pokhara.id, reason='Moving city'), 'is a batch of Pokhara')

        self._ok(self._act(ram, 'pause', reason='Moving city'))
        self._refused(self._act(ram, 'resume', batch=pokhara.id, reason='Moved'), 'is a batch of Pokhara')
        self.assertEqual(self._detail(ram)['seat_state'], 'paused')

        later = self._student()
        self._refused(self._act(later, 'join-batch', batch=pokhara.id), 'is a batch of Pokhara')
        self.assertEqual(self._seats(pokhara)[0], 0)

    def test_a_student_moved_to_another_branch_leaves_the_batch_and_joins_one_there(self):
        nov = self._batch('Python Nov')
        pokhara = self._batch('Python Pokhara', branch=self.pkr)
        ram = self._student(nov, paid='10000')
        self._ok(self.client.post(
            f'/api/students/{ram.student_id}/move-branch/',
            {'branch': self.pkr.id, 'reason': 'Family moved to Pokhara'}, format='json',
        ))
        detail = self._detail(ram)
        self.assertEqual((detail['branch'], detail['seat_state'], detail['current_batch']), (self.pkr.id, 'none', None))
        self.assertEqual(self._seats(nov)[0], 0)

        self._refused(self._act(ram, 'join-batch', batch=nov.id), 'is a batch of Kathmandu')
        data = self._ok(self._act(ram, 'join-batch', batch=pokhara.id, reason='Joins in Pokhara'))
        # The money stays on the batch he first joined, in Kathmandu.
        self.assertEqual((data['current_batch'], data['batch']), (pokhara.id, nov.id))
        self.assertEqual(self._money(nov)['figures']['students'], 1)
        self.assertEqual(self._money(pokhara)['figures']['students'], 0)

    def test_one_person_cannot_hold_two_seats_in_the_same_batch(self):
        nov = self._batch('Python Nov', seats=3)
        lead = self._lead()
        self._student(nov, lead=lead)

        again = self._enroll_response(nov, lead=lead)
        if again.status_code == 201:
            self._confirm(Enrollment.objects.get(pk=again.data['id']))
        self.assertEqual(self._seats(nov)[0], 1)

    def test_one_person_cannot_move_or_resume_into_a_batch_they_are_in(self):
        nov, dec = self._batch('Python Nov', seats=3), self._batch('Python Dec', seats=3)
        lead = self._lead()
        self._student(nov, lead=lead)
        other = self._student(dec, lead=lead)

        taken = 'already has an enrollment in Python Nov'
        self._refused(self._act(other, 'move', batch=nov.id, reason='Morning'), taken)
        self._ok(self._act(other, 'pause', reason='Exams'))
        self._refused(self._act(other, 'resume', batch=nov.id, reason='Back'), taken)
        self.assertEqual(self._seats(nov)[0], 1)


# ==================================================================== who is listed where


class BatchStudentListTests(FlowBase):
    """A batch lists who sits in it, who paused out of it and who moved out of it."""

    def test_two_moves_show_on_both_old_batches_with_where_the_student_is_now(self):
        nov, dec, jan = self._batch('Python Nov'), self._batch('Python Dec'), self._batch('Python Jan')
        ram = self._student(nov)
        self._ok(self._act(ram, 'move', batch=dec.id, reason='Evening'))
        self._ok(self._act(ram, 'move', batch=jan.id, reason='Weekend'))

        for old, went_to in ((nov, dec), (dec, jan)):
            rows = self._roster(old)['moved_out']
            self.assertEqual(
                [(r['enrollment'], r['to_batch'], r['now_batch'], r['seat_state']) for r in rows],
                [(ram.id, went_to.id, jan.id, 'seated')],
            )
        row = self._roster(jan)['seated'][0]
        self.assertEqual(
            (row['enrollment'], row['from_batch'], row['money_batch'], row['moved_in']),
            (ram.id, dec.id, nov.id, True),
        )

    def test_a_student_resumed_into_another_batch_shows_as_moved_out_of_the_old_one(self):
        nov, dec = self._batch('Python Nov'), self._batch('Python Dec')
        ram = self._student(nov)
        self._ok(self._act(ram, 'pause', reason='Exams'))
        self._ok(self._act(ram, 'resume', batch=dec.id, reason='Joins the December batch'))

        old = self._roster(nov)
        self.assertEqual((old['seated'], old['paused']), ([], []))
        self.assertEqual(
            [(r['enrollment'], r['to_batch'], r['now_batch']) for r in old['moved_out']],
            [(ram.id, dec.id, dec.id)],
        )


# ==================================================================== close and reopen


class CloseFlowTests(FlowBase):
    """Close checklist, close, money at close, and reopen."""

    def test_a_batch_from_first_student_to_close_and_reopen(self):
        # Shifts with no branch are shared by every branch.
        self._ok(self.client.post(
            '/api/batches/', {'name': 'Morning', 'start_time': '07:00', 'end_time': '09:00'}, format='json',
        ), 201)
        manager = self._user('Branch Manager')
        self._as(manager)
        shift = next(s for s in self.client.get('/api/batches/').data['results'] if s['name'] == 'Morning')

        created = self._ok(self.client.post('/api/course-batches/', {
            'name': 'Python Nov', 'course': self.python.id, 'batch': shift['id'], 'capacity': 3,
            'status': 'running', 'mode': 'online', 'trainer': 'Hari',
            'start_date': (self.today - timedelta(days=10)).isoformat(),
        }, format='json'), 201)
        self.assertEqual((created['branch'], created['batch_start_time'], created['seats_left']), (self.ktm.id, '07:00', 3))
        batch = CourseBatch.objects.get(pk=created['id'])

        ram = self._student(batch, paid='30000')
        sita = self._student(batch, paid='10000')
        late = self._enroll(batch)
        day1, day2 = self.today - timedelta(days=2), self.today - timedelta(days=1)
        self._ok(self._mark(batch, day1, [(ram, 'present'), (sita, 'present')]))
        self._ok(self._mark(batch, day2, [(ram, 'present'), (sita, 'absent')]))
        account = self._account('ram.nov@teams.test')
        self._ok(self._give(ram, account), 201)
        self._ok(self._add_cost(batch, 'trainer', '12000'), 201)

        check = self._check(batch)
        self.assertFalse(check['ready'])
        self.assertEqual([r['enrollment'] for r in check['pending']], [late.id])
        self.assertEqual([r['enrollment'] for r in check['teams']], [ram.id])
        self.assertTrue(check['attendance']['ready'])
        self.assertEqual((check['owing']['count'], money(check['owing']['total'])), (1, Decimal('20000')))
        self.assertEqual(self._close(batch).status_code, 400)

        self._ok(self._act(late, 'cancel', reason='Joined another institute'))
        self._ok(self._free(self._licence(ram)))
        self.assertTrue(self._check(batch)['ready'])

        closed = self._ok(self._close(batch))
        at_close = closed['at_close']
        self.assertEqual(at_close['students'], 2)
        self.assertEqual(
            [money(at_close[k]) for k in ('fees', 'collected', 'owed', 'costs', 'profit')],
            [Decimal('60000'), Decimal('40000'), Decimal('20000'), Decimal('12000'), Decimal('28000')],
        )
        for e in (ram, sita):
            e.refresh_from_db()
            self.assertEqual((e.status, e.current_batch_id), ('completed', batch.id))
        listed = self.client.get('/api/course-batches/').data['results']
        row = next(r for r in listed if r['id'] == batch.id)
        self.assertEqual((row['status'], row['seats_taken'], row['waiting_count']), ('closed', 2, 0))

        # Sita pays the rest after the close: it shows as after close.
        self._ok(self.client.post(self._url(sita, 'payments/'), {
            'amount': '20000', 'payment_date': timezone.now().isoformat(),
            'payment_method': 'cash', 'status': 'successful',
        }, format='json'), 201)
        data = self._money(batch)
        self.assertEqual(money(data['at_close']['owed']), Decimal('20000'))
        self.assertEqual(money(data['after_close']['collected']), Decimal('20000'))
        self.assertEqual(money(data['figures']['owed']), Decimal('0'))

        reopened = self._ok(self._reopen(batch))
        self.assertEqual((reopened['status'], reopened['at_close']), ('running', None))
        for e in (ram, sita):
            e.refresh_from_db()
            self.assertEqual(e.status, 'confirmed')

    def test_a_closed_batch_takes_no_new_student_by_any_door(self):
        nov = self._batch('Python Nov', seats=5)
        dec = self._batch('Python Dec', seats=5)
        self._student(nov)
        waiting = self._enroll(nov)
        elsewhere = self._student(dec)
        paused = self._student(dec)
        self._ok(self._act(paused, 'pause', reason='Exams'))
        unplaced = self._student()
        self._ok(self._close(nov, close_anyway=True))

        self._refused(self._enroll_response(nov), 'Python Nov is closed')
        self._refused(self._confirm(waiting), 'Python Nov is closed')
        self._refused(self._act(unplaced, 'join-batch', batch=nov.id), 'Python Nov is closed')
        self._refused(self._act(elsewhere, 'move', batch=nov.id, reason='Morning'), 'Python Nov is closed')
        self._refused(self._act(paused, 'resume', batch=nov.id, reason='Back'), 'Python Nov is closed')
        self.assertEqual(self._seats(nov), (1, 4, 1))

    def test_marks_of_a_closed_batch_cannot_be_added_or_changed(self):
        nov = self._batch('Python Nov')
        ram = self._student(nov)
        day = self.today - timedelta(days=1)
        self._ok(self._mark(nov, day, [(ram, 'present')]))
        self._ok(self._close(nov))

        self._refused(self._mark(nov, day, [(ram, 'absent')]), 'This batch is closed')
        self._refused(self._mark(nov, self.today, [(ram, 'present')]), 'This batch is closed')
        register = self.client.get(self._batch_url(nov, 'attendance/'), {'date': day.isoformat()}).data
        self.assertTrue(register['closed'])
        self.assertEqual([r['status'] for r in register['students']], ['present'])

    def test_costs_added_and_voided_after_close_show_as_after_close(self):
        nov = self._batch('Python Nov')
        self._student(nov, paid='10000')
        trainer = self._ok(self._add_cost(nov, 'trainer', '4000'), 201)['costs'][0]
        self._ok(self._close(nov, close_anyway=True))

        self._ok(self._add_cost(nov, 'venue', '1000'), 201)
        self._ok(self.client.post(
            self._batch_url(nov, f'costs/{trainer["id"]}/void/'), {'reason': 'Billed to December'}, format='json',
        ))
        data = self._money(nov)
        self.assertEqual((money(data['at_close']['costs']), money(data['at_close']['profit'])),
                         (Decimal('4000'), Decimal('6000')))
        self.assertEqual((money(data['figures']['costs']), money(data['figures']['profit'])),
                         (Decimal('1000'), Decimal('9000')))
        self.assertEqual((money(data['after_close']['costs']), money(data['after_close']['profit'])),
                         (Decimal('-3000'), Decimal('3000')))

    def test_a_pending_student_left_on_a_closed_batch_changes_batch_and_confirms(self):
        nov, dec = self._batch('Python Nov'), self._batch('Python Dec')
        waiting = self._enroll(nov)
        self._ok(self._close(nov, close_anyway=True))
        self._refused(self._confirm(waiting), 'Python Nov is closed')

        self._ok(self.client.patch(self._url(waiting), {'batch': dec.id}, format='json'))
        data = self._ok(self._confirm(waiting))
        self.assertEqual((data['current_batch'], data['batch']), (dec.id, dec.id))
        self.assertEqual(self._seats(nov), (0, 2, 0))
        self.assertEqual(self._seats(dec), (1, 1, 0))

    def test_reopen_gives_back_only_the_students_still_in_the_batch(self):
        nov, dec = self._batch('Python Nov'), self._batch('Python Dec')
        stays, leaves = self._student(nov), self._student(nov)
        self._ok(self._close(nov, close_anyway=True))
        # Moving out of a closed batch is allowed.
        self._ok(self._act(leaves, 'move', batch=dec.id, reason='Needs more practice'))

        self._ok(self._reopen(nov))
        stays.refresh_from_db()
        leaves.refresh_from_db()
        self.assertEqual((stays.status, stays.current_batch_id), ('confirmed', nov.id))
        self.assertEqual((leaves.status, leaves.current_batch_id), ('completed', dec.id))

    def test_money_of_a_moved_student_stays_on_the_first_batch_when_the_new_one_closes(self):
        nov, dec = self._batch('Python Nov'), self._batch('Python Dec')
        mover = self._student(nov, paid='10000')
        self._ok(self._act(mover, 'move', batch=dec.id, reason='Evening'))
        local = self._student(dec, paid='5000')

        closed = self._ok(self._close(dec, close_anyway=True))
        self.assertEqual((closed['at_close']['students'], money(closed['at_close']['collected'])), (1, Decimal('5000')))
        self.assertEqual(
            [c['enrollment'] for c in closed['closings'][0]['checklist']['completed']], [mover.id, local.id],
        )
        first = self._money(nov)['figures']
        self.assertEqual((first['students'], money(first['collected'])), (1, Decimal('10000')))
        check = self._check(nov)
        self.assertEqual(check['completing'], [])
        self.assertEqual([(p['enrollment'], money(p['owed'])) for p in check['owing']['people']],
                         [(mover.id, Decimal('20000'))])

    def test_a_teams_licence_is_safe_to_remove_after_close_and_in_use_after_reopen(self):
        nov = self._batch('Python Nov')
        ram = self._student(nov)
        self._ok(self._give(ram, self._account('ram@teams.test')), 201)
        self.assertEqual([r['enrollment'] for r in self._check(nov)['teams']], [ram.id])
        self._refused(self._close(nov), 'close anyway')

        closed = self._ok(self._close(nov, close_anyway=True))
        self.assertEqual(closed['closings'][0]['checklist']['teams'], [ram.lead.full_name])
        licence = self._licence(ram)  # closing never takes a licence back
        self.assertEqual(self._licence_state(licence), ('safe_to_remove', 'Course completed'))
        self.assertEqual(self._summary()['safe_to_remove'], 1)

        self._ok(self._reopen(nov))
        self.assertEqual(self._licence_state(licence), ('in_use', ''))

    def test_close_check_counts_only_the_class_days_of_this_batch(self):
        nov, dec = self._batch('Python Nov'), self._batch('Python Dec')
        mover = self._student(nov)
        stayer = self._student()
        self._ok(self._act(
            stayer, 'join-batch', batch=dec.id, started_on=(self.today - timedelta(days=10)).isoformat(),
        ))
        nov_class, dec_class = self.today - timedelta(days=3), self.today - timedelta(days=2)
        self._ok(self._mark(nov, nov_class, [(mover, 'present')]))
        self._ok(self._mark(dec, dec_class, [(stayer, 'present')]))
        self._ok(self._act(mover, 'move', batch=dec.id, reason='Evening suits him'))

        attendance = self._check(dec)['attendance']
        self.assertEqual(attendance['missing'], [])
        self.assertEqual(attendance['days'], 1)


# ==================================================================== register


class RegisterTests(FlowBase):
    """The batch register: who can be marked, on which days, and through which door."""

    def test_only_students_sitting_in_the_batch_now_can_be_marked(self):
        nov, dec = self._batch('Python Nov'), self._batch('Python Dec')
        ram = self._student(nov)
        self._ok(self._act(ram, 'pause', reason='Exams'))
        self._refused(self._mark(nov, self.today, [(ram, 'present')]), 'not in this batch')

        self._ok(self._act(ram, 'resume', batch=dec.id, reason='Exams over'))
        self._ok(self._mark(dec, self.today, [(ram, 'present')]))
        self._refused(self._mark(nov, self.today, [(ram, 'present')]), 'not in this batch')

    def test_a_day_after_the_batch_ended_is_refused(self):
        nov = self._batch('Python Nov', started=10, ends=-3)
        ram = self._student(nov)
        response = self._mark(nov, self.today - timedelta(days=1), [(ram, 'present')])
        self.assertEqual(response.status_code, 400, response.data)

    def test_the_student_attendance_list_cannot_mark_a_future_day(self):
        nov = self._batch('Python Nov')
        ram = self._student(nov)
        self._as(self._user('Trainer'))
        response = self.client.post(f'/api/students/{ram.student_id}/attendance/', {
            'course': self.python.id, 'date': (self.today + timedelta(days=1)).isoformat(), 'status': 'present',
        }, format='json')
        self.assertEqual(response.status_code, 400, response.data)

    def test_a_mark_of_a_closed_batch_cannot_be_changed_through_the_attendance_list(self):
        nov = self._batch('Python Nov')
        ram = self._student(nov)
        day = self.today - timedelta(days=1)
        self._ok(self._mark(nov, day, [(ram, 'present')]))
        self._ok(self._close(nov))
        mark = Attendance.objects.get(student_id=ram.student_id, date=day)

        self._as(self._user('Trainer'))
        response = self.client.patch(f'/api/attendance/{mark.id}/', {'status': 'absent'}, format='json')
        self.assertIn(response.status_code, (400, 403))
        mark.refresh_from_db()
        self.assertEqual(mark.status, 'present')


# ==================================================================== roles and branches


class RoleTests(FlowBase):
    """The default roles can do their part of the flow and nothing more."""

    def test_trainer_marks_and_corrects_attendance_but_cannot_close_move_or_see_money(self):
        nov, dec = self._batch('Python Nov'), self._batch('Python Dec')
        ram, sita = self._student(nov, paid='1000'), self._student(nov)
        self._as(self._user('Trainer'))

        self._ok(self._mark(nov, self.today, [(ram, 'present'), (sita, 'absent')]))
        changed = self._ok(self._mark(nov, self.today, [(sita, 'present')]))
        self.assertEqual([r['status'] for r in changed['students']], ['present', 'present'])
        self.assertIsNone(self._check(nov)['owing'])
        self._roster(nov)

        for response in (
            self.client.get(self._batch_url(nov, 'money/')),
            self._close(nov, close_anyway=True),
            self._add_cost(nov, 'trainer', '100'),
            self._act(ram, 'move', batch=dec.id, reason='Evening'),
        ):
            self.assertEqual(response.status_code, 403, response.data)

    def test_counselor_seats_students_but_cannot_take_attendance_or_close(self):
        nov, dec = self._batch('Python Nov'), self._batch('Python Dec')
        self._as(self._user('Counselor'))
        ram = self._enroll(nov)
        self._ok(self._confirm(ram))
        self._ok(self._act(ram, 'move', batch=dec.id, reason='Evening'))
        ram.refresh_from_db()

        for response in (
            self.client.get(self._batch_url(dec, 'attendance/')),
            self._mark(dec, self.today, [(ram, 'present')]),
            self._close(dec, close_anyway=True),
            self.client.get(self._batch_url(dec, 'money/')),
        ):
            self.assertEqual(response.status_code, 403, response.data)

    def test_accountant_handles_costs_but_cannot_close_or_take_attendance(self):
        nov = self._batch('Python Nov')
        ram = self._student(nov, paid='10000')
        self._as(self._user('Accountant'))

        cost = self._ok(self._add_cost(nov, 'venue', '2500'), 201)['costs'][0]
        voided = self._ok(self.client.post(
            self._batch_url(nov, f'costs/{cost["id"]}/void/'), {'reason': 'Wrong batch'}, format='json',
        ))
        self.assertEqual(money(voided['figures']['costs']), Decimal('0'))
        self.assertEqual(money(self._money(nov)['figures']['collected']), Decimal('10000'))
        self.assertEqual(self._close(nov, close_anyway=True).status_code, 403)
        self.assertEqual(self._mark(nov, self.today, [(ram, 'present')]).status_code, 403)

    def test_a_branch_manager_cannot_see_or_work_another_branch_batch(self):
        pokhara = self._batch('Python Pokhara', branch=self.pkr)
        far = self._student(pokhara, lead=self._lead(self.pkr))
        self._as(self._user('Branch Manager', self.ktm))

        listed = self.client.get('/api/course-batches/').data['results']
        self.assertNotIn(pokhara.id, [r['id'] for r in listed])
        for response in (
            self.client.get(self._batch_url(pokhara)),
            self.client.get(self._batch_url(pokhara, 'students/')),
            self.client.get(self._batch_url(pokhara, 'close-check/')),
            self.client.get(self._batch_url(pokhara, 'attendance/')),
            self.client.get(self._batch_url(pokhara, 'money/')),
            self._close(pokhara, close_anyway=True),
            self._add_cost(pokhara, 'venue', '100'),
            self._mark(pokhara, self.today, [(far, 'present')]),
            self.client.patch(self._batch_url(pokhara), {'capacity': 50}, format='json'),
            self._act(far, 'pause', reason='Exams'),
        ):
            self.assertEqual(response.status_code, 404, response.data)
        pokhara.refresh_from_db()
        self.assertEqual((pokhara.status, pokhara.capacity), ('running', 2))

    def test_a_branch_manager_cannot_put_a_batch_in_another_branch(self):
        self._as(self._user('Branch Manager', self.ktm))
        self.client.post('/api/course-batches/', {
            'name': 'Python Pokhara', 'course': self.python.id, 'capacity': 10, 'branch': self.pkr.id,
        }, format='json')
        mine = self._ok(self.client.post('/api/course-batches/', {
            'name': 'Python Nov', 'course': self.python.id, 'capacity': 10,
        }, format='json'), 201)
        self.client.patch(f'/api/course-batches/{mine["id"]}/', {'branch': self.pkr.id}, format='json')
        self.assertFalse(CourseBatch.objects.filter(branch=self.pkr).exists())

    def test_a_batch_with_students_cannot_move_to_another_branch(self):
        nov = self._batch('Python Nov')
        self._student(nov)
        response = self.client.patch(self._batch_url(nov), {'branch': self.pkr.id}, format='json')
        self.assertEqual(response.status_code, 400, response.data)

    def test_a_branch_manager_cannot_change_a_shared_shift(self):
        shared = self._ok(self.client.post(
            '/api/batches/', {'name': 'Morning', 'start_time': '07:00', 'end_time': '09:00'}, format='json',
        ), 201)
        self.assertIsNone(shared['branch'])
        self._as(self._user('Branch Manager', self.ktm))
        response = self.client.patch(f'/api/batches/{shared["id"]}/', {'end_time': '10:00'}, format='json')
        self.assertIn(response.status_code, (400, 403, 404))
        self.assertEqual(Batch.objects.get(pk=shared['id']).end_time.strftime('%H:%M'), '09:00')

    def test_a_branch_manager_changes_only_their_own_branch_shifts(self):
        self._as(self._user('Branch Manager', self.ktm))
        shift = {'name': 'Evening', 'start_time': '17:00', 'end_time': '19:00'}
        self._refused(
            self.client.post('/api/batches/', {**shift, 'branch': self.pkr.id}, format='json'), 'your own branch',
        )
        own = self._ok(self.client.post('/api/batches/', shift, format='json'), 201)
        self.assertEqual(own['branch'], self.ktm.id)

        url = f'/api/batches/{own["id"]}/'
        self._ok(self.client.patch(url, {'end_time': '20:00'}, format='json'))
        self._refused(self.client.patch(url, {'branch': None}, format='json'), 'Only staff')
        self.assertEqual(Batch.objects.get(pk=own['id']).branch_id, self.ktm.id)


# ==================================================================== Teams licences


class TeamsFlowTests(FlowBase):
    """A Teams licence follows the student through pause, cancel, close and reinstate."""

    def test_one_account_passes_from_student_to_student(self):
        nov = self._batch('Python Nov')
        ram, sita = self._student(nov), self._student(nov)
        first = self._account('pool1@teams.test', self.ktm)
        self._ok(self._give(ram, first), 201)
        self.assertEqual(self._summary(), {
            'total': 1, 'ready': 0, 'in_use': 1, 'safe_to_remove': 0, 'action_required': 0, 'retired': 0,
        })
        self._refused(self._give(sita, first), 'pool1@teams.test is in use by Student 01')

        second = self._account('pool2@teams.test', self.ktm)
        self._refused(self._give(ram, second), 'This student already has a Teams licence')

        licence = self._licence(ram)
        self._ok(self._free(licence))
        self._refused(self._free(licence), 'This licence was already taken back')
        self.assertEqual(self._summary()['ready'], 2)
        self._ok(self._give(sita, first), 201)

        self._ok(self.client.post(
            f'/api/teams-accounts/{second.id}/retire/', {'reason': 'Removed from Microsoft 365'}, format='json',
        ))
        self._refused(self._give(ram, second), 'pool2@teams.test is retired')
        self.assertEqual(self._summary(), {
            'total': 1, 'ready': 0, 'in_use': 1, 'safe_to_remove': 0, 'action_required': 0, 'retired': 1,
        })

    def test_a_paused_student_keeps_a_paused_licence_and_gets_it_back_in_another_batch(self):
        nov, dec = self._batch('Python Nov'), self._batch('Python Dec')
        ram = self._student(nov)
        self._ok(self._give(ram, self._account('ram@teams.test')), 201)
        self._ok(self._act(ram, 'pause', reason='Exams'))

        licence = self._licence(ram)
        self.assertIsNotNone(licence.paused_at)
        self.assertEqual(self._licence_state(licence), ('action_required', 'Paused'))
        self.assertEqual(self._check(nov)['teams'], [])
        self._refused(self._give(ram, self._account('spare@teams.test')), 'Only a student sitting in a batch')

        self._ok(self._act(ram, 'resume', batch=dec.id, reason='Exams over'))
        licence.refresh_from_db()
        self.assertIsNone(licence.paused_at)
        self.assertEqual(self._licence_state(licence), ('in_use', ''))

    def test_a_cancelled_student_keeps_the_licence_until_someone_frees_it(self):
        nov = self._batch('Python Nov')
        ram = self._student(nov)
        account = self._account('ram@teams.test')
        self._ok(self._give(ram, account), 201)
        self._ok(self._act(ram, 'cancel', reason='Moved abroad'))

        licence = self._licence(ram)  # cancel never takes a licence back
        self.assertEqual(self._licence_state(licence), ('action_required', 'Enrollment cancelled'))
        self.assertEqual(self._summary()['action_required'], 1)
        self._ok(self._free(licence, reason='Left the course'))
        self.assertEqual(self._summary()['ready'], 1)

        self._ok(self._act(ram, 'reinstate', reason='Came back'))
        self._refused(self._give(ram, account), 'Only a student sitting in a batch')
        self._ok(self._act(ram, 'join-batch', batch=nov.id))
        self._ok(self._give(ram, account), 201)

    def test_a_paused_then_cancelled_then_reinstated_student_resumes_with_the_licence(self):
        nov = self._batch('Python Nov')
        ram = self._student(nov)
        self._ok(self._give(ram, self._account('ram@teams.test')), 201)
        self._ok(self._act(ram, 'pause', reason='Exams'))
        self._ok(self._act(ram, 'cancel', reason='Not coming back'))
        self._ok(self._act(ram, 'reinstate', reason='Coming back after all'))

        detail = self._detail(ram)
        self.assertEqual((detail['status'], detail['seat_state']), ('confirmed', 'paused'))
        self.assertEqual(detail['teams_licence']['state'], 'action_required')
        self._refused(self._act(ram, 'join-batch', batch=nov.id), 'Use Resume')
        self._ok(self._act(ram, 'resume', batch=nov.id, reason='Back'))
        self.assertEqual(self._detail(ram)['teams_licence']['state'], 'in_use')

    def test_a_branch_manager_cannot_give_or_free_licences_of_another_branch(self):
        nov = self._batch('Python Nov')
        ram, sita = self._student(nov), self._student(nov)
        pokhara_account = self._account('pkr@teams.test', self.pkr)
        self._ok(self._give(ram, self._account('ktm@teams.test', self.ktm)), 201)
        self._refused(self._give(sita, pokhara_account), 'pkr@teams.test belongs to Pokhara')

        self._as(self._user('Branch Manager', self.pkr))
        self._refused(self._give(sita, pokhara_account), 'This enrollment belongs to another branch')
        self.assertEqual(self._free(self._licence(ram)).status_code, 404)
        self.assertTrue(self._licence(ram).is_open)
