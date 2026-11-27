"""Money flow through the API, end to end, the way staff use it.

Enrollment -> payment -> confirm -> invoices and receipts -> money entries ->
due dates -> history -> reminders -> money reports. Single rules already
covered in apps/lead/tests.py, apps/invoices/tests.py and
apps/leadreport/tests.py are not repeated here; these tests cross features
and check that every screen shows the same money.

A test marked expectedFailure shows a bug; its # BUG: line says what is wrong.
"""
import unittest
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import mail
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.branch.models import Branch
from apps.course.models import Course, CourseBatch
from apps.invoices.models import Invoice
from apps.lead.models import Enrollment, Lead, Payment, PaymentPlan, ReminderConfig
from apps.lead.reminders import send_payment_reminders
from apps.student.models import Student

User = get_user_model()
UTC = dt_timezone.utc


def money(value):
    return Decimal(str(value))


class MoneyFlowBase(APITestCase):
    """Kathmandu and Pokhara branches, a Python course (30000) and a Kathmandu batch."""

    def setUp(self):
        self.admin = User.objects.create_user(
            email='flow-admin@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.ktm = Branch.objects.create(name='Kathmandu', code='KTM')
        self.pkr = Branch.objects.create(name='Pokhara', code='PKR')
        self.course = Course.objects.create(name='Python', full_amount=30000)
        self.batch = CourseBatch.objects.create(
            name='Python Nov', course=self.course, capacity=10, status='running', branch=self.ktm,
        )
        self.today = timezone.localdate()
        self.people = 0

    # ---- people

    def lead(self, branch=None):
        self.people += 1
        return Lead.objects.create(
            full_name=f'Flow Student {self.people}', phone=f'98111000{self.people:02d}',
            email=f'flow{self.people}@example.com', branch=branch or self.ktm,
        )

    def role_user(self, role, branch=None):
        """A user with one of the default roles (apps/account/migrations/0010_default_roles.py)."""
        user = User.objects.create_user(
            email=f'{role.replace(" ", "").lower()}{User.objects.count()}@test.com',
            name=role, password='pass', branch=branch or self.ktm,
        )
        user.groups.add(Group.objects.get(name=role))
        return User.objects.get(pk=user.pk)

    # ---- calls

    def url(self, enrollment, tail=''):
        return f'/api/leads/{enrollment.lead_id}/enrollments/{enrollment.id}/{tail}'

    def day(self, days):
        return str(self.today + timedelta(days=days))

    def enroll(self, lead=None, batch=None, course=None, agreed='30000', **extra):
        lead = lead or self.lead()
        body = {
            'course': (course or self.course).id, 'enrollment_date': str(self.today),
            'agreed_amount': agreed, **extra,
        }
        if batch is not None:
            body['batch'] = batch.id
        response = self.client.post(f'/api/leads/{lead.id}/enrollments/', body, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return Enrollment.objects.get(pk=response.data['id'])

    def confirm(self, enrollment, paid='5000', due_dates=None, method='cash'):
        body = {}
        if paid:
            body['first_payment'] = {'amount': paid, 'payment_method': method}
        if due_dates is not None:
            body['due_dates'] = due_dates
        return self.client.post(self.url(enrollment, 'confirm/'), body, format='json')

    def confirmed(self, paid='5000', due_dates=None, **enroll):
        enrollment = self.enroll(**enroll)
        response = self.confirm(enrollment, paid=paid, due_dates=due_dates)
        self.assertEqual(response.status_code, 200, response.data)
        enrollment.refresh_from_db()
        return enrollment

    def pay(self, enrollment, amount, status='successful', when=None):
        return self.client.post(self.url(enrollment, 'payments/'), {
            'amount': amount, 'payment_date': (when or timezone.now()).isoformat(),
            'payment_method': 'cash', 'status': status,
        }, format='json')

    def paid_id(self, enrollment, amount, **kwargs):
        response = self.pay(enrollment, amount, **kwargs)
        self.assertEqual(response.status_code, 201, response.data)
        return response.data['id']

    def void(self, enrollment, payment_id, reason='Typed the wrong amount'):
        return self.client.post(
            self.url(enrollment, f'payments/{payment_id}/void/'), {'reason': reason}, format='json',
        )

    def entry(self, enrollment, kind, amount, reason='Agreed with the manager'):
        return self.client.post(
            self.url(enrollment, 'adjustments/'),
            {'kind': kind, 'amount': amount, 'reason': reason}, format='json',
        )

    def added(self, enrollment, kind, amount, reason='Agreed with the manager'):
        response = self.entry(enrollment, kind, amount, reason)
        self.assertEqual(response.status_code, 201, response.data)

    def due_dates(self, enrollment, parts, reason='Asked for more time'):
        return self.client.post(
            self.url(enrollment, 'due-dates/'), {'due_dates': parts, 'reason': reason}, format='json',
        )

    def cancel_invoice(self, invoice, reason='Wrong name on it'):
        response = self.client.post(f'/api/invoices/{invoice.id}/cancel/', {'reason': reason}, format='json')
        self.assertEqual(response.status_code, 200, response.data)

    # ---- what the screens show

    def card(self, enrollment):
        response = self.client.get(self.url(enrollment))
        self.assertEqual(response.status_code, 200)
        return response.data

    def history(self, enrollment):
        response = self.client.get(self.url(enrollment, 'history/'), {'page_size': 100})
        self.assertEqual(response.status_code, 200)
        return response.data['results']

    def report(self, name, **params):
        response = self.client.get(f'/api/reports/{name}/', params)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('error', response.data)
        return response.data

    def owed_row(self, enrollment):
        rows = [r for r in self.report('who-owes')['results'] if r['enrollment'] == enrollment.id]
        return rows[0] if rows else None

    def batch_row(self, batch):
        return next(r for r in self.report('batch-profit')['results'] if r['batch'] == batch.id)

    def first_payment(self, enrollment):
        return Payment.objects.filter(enrollment=enrollment).order_by('id').first()


class HappyPathTests(MoneyFlowBase):
    """A lead pays in three parts and becomes a fully paid student."""

    def test_lead_pays_in_parts_and_every_screen_agrees(self):
        lead = self.lead()
        enrollment = self.enroll(lead=lead, batch=self.batch)
        self.assertEqual(enrollment.status, 'pending')
        self.assertFalse(Student.objects.exists())

        response = self.confirm(enrollment, paid='10000', method='esewa', due_dates=[
            {'due_on': self.day(60), 'amount': '10000'},
            {'due_on': self.day(0), 'amount': '10000'},
            {'due_on': self.day(30), 'amount': '10000'},
        ])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], 'confirmed')
        self.assertEqual([r['status'] for r in response.data['due_dates']], ['paid', 'due', 'due'])
        student = Student.objects.get(lead=lead)
        lead.refresh_from_db()
        self.assertTrue(lead.is_converted)
        fee = Invoice.objects.get(enrollment=enrollment, type='enrollment')
        self.assertEqual(
            (fee.status, fee.amount, fee.total_paid_so_far, fee.remaining_balance, fee.student),
            ('active', Decimal('30000'), Decimal('10000'), Decimal('20000'), student),
        )

        self.paid_id(enrollment, '10000')
        pending_id = self.paid_id(enrollment, '10000', status='pending')
        self.assertEqual(self.card(enrollment)['remaining_amount'], '10000.00')
        response = self.client.post(self.url(enrollment, f'payments/{pending_id}/confirm/'))
        self.assertEqual(response.status_code, 200, response.data)

        card = self.card(enrollment)
        self.assertEqual(
            (card['total_paid'], card['remaining_amount'], card['is_full_paid']), ('30000.00', '0.00', True),
        )
        self.assertEqual([r['status'] for r in card['due_dates']], ['paid', 'paid', 'paid'])
        receipts = Invoice.objects.filter(enrollment=enrollment, type='payment').order_by('id')
        self.assertEqual(
            [(r.amount, r.total_paid_so_far, r.remaining_balance, r.status) for r in receipts],
            [
                (Decimal('10000'), Decimal('10000'), Decimal('20000'), 'active'),
                (Decimal('10000'), Decimal('20000'), Decimal('10000'), 'active'),
                (Decimal('10000'), Decimal('30000'), Decimal('0'), 'active'),
            ],
        )
        self.assertTrue(all(r.invoice_number.startswith(f'RCT-{self.today.year}-') for r in receipts))

        self.assertIsNone(self.owed_row(enrollment))
        revenue = self.report('revenue')['summary']
        self.assertEqual(money(revenue['total_collected']), 30000)
        self.assertEqual(money(revenue['outstanding_to_collect']), 0)
        row = self.batch_row(self.batch)
        self.assertEqual(
            (row['students'], money(row['fees']), money(row['collected']), money(row['owed'])),
            (1, 30000, 30000, 0),
        )
        self.assertEqual(self.client.get('/api/payments/summary/').data['successful_total'], '30000.00')
        self.assertEqual(money(self.client.get(f'/api/students/{student.id}/').data['total_paid']), 30000)
        # Payments are not history rows; the confirm is.
        self.assertEqual([r['action'] for r in self.history(enrollment)], ['confirm'])


class FiguresAgreeTests(MoneyFlowBase):
    """The enrollment card, lists, who owes, due in month, revenue, batch profit and
    the student record show the same money."""

    def test_every_screen_shows_the_same_balance_after_mixed_money_actions(self):
        enrollment = self.confirmed(paid='10000', batch=self.batch, due_dates=[
            {'due_on': self.day(-40), 'amount': '10000'},
            {'due_on': self.day(-10), 'amount': '10000'},
            {'due_on': self.day(20), 'amount': '10000'},
        ])
        self.added(enrollment, 'extra_charge', '2000', 'Exam fee')
        self.added(enrollment, 'discount', '3000', 'Festival offer')
        self.added(enrollment, 'write_off', '1000', 'Manager approved')
        self.paid_id(enrollment, '4000')
        wrong = self.paid_id(enrollment, '6000')
        self.assertEqual(self.void(enrollment, wrong).status_code, 200)
        self.paid_id(enrollment, '2000', status='pending')  # not money yet

        # 30000 + 2000 - 3000 - 1000 - (10000 + 4000) = 14000
        card = self.card(enrollment)
        self.assertEqual((card['total_paid'], card['remaining_amount']), ('14000.00', '14000.00'))
        self.assertEqual(
            [(r['kind'], r['left'], r['status']) for r in card['due_dates']],
            [
                ('part', '0.00', 'paid'),
                ('part', '2000.00', 'late'),
                ('extra_charge', '2000.00', 'due'),
                ('part', '10000.00', 'due'),
            ],
        )

        row = self.owed_row(enrollment)
        self.assertEqual(
            (row['owed'], row['late'], row['days_late'], row['next_due_on'], row['next_due_amount']),
            ('14000.00', '2000.00', 10, str(self.today), '2000.00'),
        )
        listed = self.client.get('/api/enrollments/', {'lead': enrollment.lead_id}).data['results']
        self.assertEqual((listed[0]['total_paid'], listed[0]['remaining_amount']), ('14000.00', '14000.00'))
        revenue = self.report('revenue')['summary']
        self.assertEqual(money(revenue['total_collected']), 14000)
        self.assertEqual(money(revenue['outstanding_to_collect']), 14000)
        batch = self.batch_row(self.batch)
        self.assertEqual((money(batch['fees']), money(batch['collected']), money(batch['owed'])), (28000, 14000, 14000))
        payments = self.client.get('/api/payments/summary/', {'enrollment': enrollment.id}).data
        self.assertEqual((payments['count'], payments['successful_total']), (4, '14000.00'))
        student = self.client.get(f'/api/students/{enrollment.student_id}/').data
        self.assertEqual(money(student['total_paid']), 14000)

        month = self.today.strftime('%Y-%m')
        expected = sum(
            (money(r['left']) for r in card['due_dates'] if r['due_on'].startswith(month)), Decimal('0'),
        )
        self.assertEqual(money(self.report('due-in-month', month=month)['summary']['total_due']), expected)

    def test_voided_payments_and_cancelled_enrollments_stay_out_of_what_is_owed(self):
        owing = self.confirmed(paid='10000', batch=self.batch)
        self.void(owing, self.paid_id(owing, '5000'))
        gone = self.confirmed(paid='8000', batch=self.batch)
        cancel = self.client.post(self.url(gone, 'cancel/'), {'reason': 'Moved abroad'}, format='json')
        self.assertEqual(cancel.status_code, 200)

        who = self.report('who-owes')
        self.assertEqual([r['enrollment'] for r in who['results']], [owing.id])
        self.assertEqual(who['summary']['total_owed'], '20000.00')
        revenue = self.report('revenue')['summary']
        self.assertEqual(money(revenue['total_collected']), 18000)  # money kept from the cancelled one counts
        self.assertEqual(money(revenue['outstanding_to_collect']), 20000)
        batch = self.batch_row(self.batch)
        self.assertEqual((batch['students'], money(batch['collected']), money(batch['owed'])), (1, 18000, 20000))
        self.assertEqual(self.client.get('/api/payments/summary/').data['successful_total'], '18000.00')
        receipts = Invoice.objects.filter(type='payment')
        self.assertEqual(sorted(receipts.values_list('status', flat=True)), ['active', 'active', 'cancelled'])

    def test_cancel_and_reinstate_keep_the_money_and_the_due_dates(self):
        enrollment = self.confirmed(paid='10000', due_dates=[
            {'due_on': self.day(-5), 'amount': '15000'}, {'due_on': self.day(25), 'amount': '15000'},
        ])
        before = self.owed_row(enrollment)
        self.client.post(self.url(enrollment, 'cancel/'), {'reason': 'Family trouble'}, format='json')
        self.assertIsNone(self.owed_row(enrollment))
        self.assertEqual(self.card(enrollment)['due_dates'], [])
        self.client.post(self.url(enrollment, 'reinstate/'), {'reason': 'Came back'}, format='json')
        after = self.owed_row(enrollment)
        self.assertEqual(
            (after['owed'], after['late'], after['next_due_on']),
            (before['owed'], before['late'], before['next_due_on']),
        )

    def test_student_total_paid_is_the_sum_of_their_enrollments(self):
        java = Course.objects.create(name='Java', full_amount=20000)
        lead = self.lead()
        python = self.confirmed(lead=lead, paid='10000')
        second = self.confirmed(lead=lead, course=java, agreed='20000', paid='4000')
        self.void(python, self.paid_id(python, '5000'))
        self.paid_id(second, '1000', status='pending')

        student = Student.objects.get(lead=lead)
        data = self.client.get(f'/api/students/{student.id}/').data
        cards = [self.card(python), self.card(second)]
        self.assertEqual(money(data['total_paid']), sum(money(c['total_paid']) for c in cards))
        self.assertEqual(money(data['total_paid']), 14000)
        self.assertEqual(data['enrollment_count'], 2)


class RevenueReportTests(MoneyFlowBase):
    """The revenue report against who owes and batch profit."""

    def test_revenue_collected_takes_off_refunds_like_batch_profit(self):
        enrollment = self.confirmed(paid='30000', batch=self.batch)
        self.added(enrollment, 'discount', '10000', 'Moved to a cheaper plan')
        self.added(enrollment, 'refund', '10000', 'Gave back the difference')
        self.assertEqual(money(self.batch_row(self.batch)['collected']), 20000)
        self.assertEqual(money(self.report('revenue')['summary']['total_collected']), 20000)

    def test_revenue_outstanding_matches_who_owes(self):
        self.confirmed(paid='5000')
        self.enroll()  # pending: not confirmed and nothing paid
        owed = self.report('who-owes')['summary']['total_owed']
        self.assertEqual(owed, '25000.00')
        self.assertEqual(money(self.report('revenue')['summary']['outstanding_to_collect']), money(owed))


class VoidAndRefundTests(MoneyFlowBase):
    """Voids and refunds on the same enrollment."""

    def test_voiding_a_refunded_payment_keeps_refunds_within_what_was_paid(self):
        enrollment = self.confirmed(paid='30000')
        self.added(enrollment, 'discount', '10000', 'Course price dropped')
        self.added(enrollment, 'refund', '10000', 'Gave back the difference')
        response = self.void(enrollment, self.first_payment(enrollment).id, reason='Payment was entered twice')
        self.assertEqual(response.status_code, 400)
        card = self.card(enrollment)
        self.assertGreaterEqual(money(card['total_paid']), money(card['refunds_total']))
        self.assertLessEqual(money(card['remaining_amount']), 20000)  # never more than the fee after the discount

    def test_voiding_one_payment_is_fine_when_another_covers_the_refund(self):
        enrollment = self.confirmed(paid='10000')
        duplicate = self.paid_id(enrollment, '10000')
        self.added(enrollment, 'discount', '15000', 'Scholarship')
        self.added(enrollment, 'refund', '5000', 'Gave back the extra')
        self.assertEqual(self.void(enrollment, duplicate, reason='Entered twice').status_code, 200)
        card = self.card(enrollment)
        # 30000 - 15000 - 10000 + 5000
        self.assertEqual(
            (card['total_paid'], card['refunds_total'], card['remaining_amount']),
            ('10000.00', '5000.00', '10000.00'),
        )
        self.assertEqual(self.owed_row(enrollment)['owed'], '10000.00')

    def test_a_voided_pending_payment_cannot_be_confirmed_later(self):
        enrollment = self.confirmed(paid='5000')
        pending = self.paid_id(enrollment, '5000', status='pending')
        self.assertEqual(self.void(enrollment, pending, reason='Cheque never came').status_code, 200)
        response = self.client.post(self.url(enrollment, f'payments/{pending}/confirm/'))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['detail'], 'Only a pending payment can be confirmed.')
        self.assertFalse(Invoice.objects.filter(payment_id=pending).exists())
        self.assertEqual(self.card(enrollment)['total_paid'], '5000.00')


class BranchAndRightsTests(MoneyFlowBase):
    """Branch walls and role rights on money actions."""

    def pokhara_enrollment(self):
        """A Kathmandu lead who enrolled at Pokhara (staff picked the branch), confirmed with 5000."""
        enrollment = self.enroll(lead=self.lead(branch=self.ktm), branch=self.pkr.id)
        self.assertEqual(enrollment.branch, self.pkr)
        self.assertEqual(self.confirm(enrollment).status_code, 200)
        return enrollment

    def test_branch_user_cannot_see_or_change_another_branchs_enrollment_money(self):
        enrollment = self.pokhara_enrollment()
        payment = self.first_payment(enrollment)
        self.client.force_authenticate(self.role_user('Accountant', branch=self.ktm))
        self.assertEqual(self.client.get(self.url(enrollment)).status_code, 404)
        self.assertEqual(self.entry(enrollment, 'refund', '100').status_code, 404)
        self.assertEqual(self.void(enrollment, payment.id).status_code, 404)
        self.assertEqual(self.due_dates(enrollment, [{'due_on': self.day(5), 'amount': '30000'}]).status_code, 404)
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'successful')

    def test_branch_user_cannot_pay_into_another_branchs_enrollment(self):
        enrollment = self.pokhara_enrollment()
        self.client.force_authenticate(self.role_user('Accountant', branch=self.ktm))
        response = self.pay(enrollment, '1000')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Payment.objects.filter(enrollment=enrollment).count(), 1)

    def test_counselor_confirms_once_the_accountant_has_taken_the_payment(self):
        counselor = self.role_user('Counselor')
        accountant = self.role_user('Accountant')
        self.client.force_authenticate(counselor)
        enrollment = self.enroll()
        self.assertEqual(self.pay(enrollment, '5000').status_code, 403)
        self.client.force_authenticate(accountant)
        payment_id = self.paid_id(enrollment, '5000')
        self.client.force_authenticate(counselor)
        self.assertEqual(self.confirm(enrollment, paid=None).status_code, 200)
        self.assertEqual(self.entry(enrollment, 'discount', '1000').status_code, 403)
        self.assertEqual(self.void(enrollment, payment_id).status_code, 403)

    def test_counselor_cannot_take_money_through_confirm(self):
        self.client.force_authenticate(self.role_user('Counselor'))
        enrollment = self.enroll()
        response = self.confirm(enrollment, paid='5000')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Payment.objects.filter(enrollment=enrollment).exists())

    def test_only_money_roles_add_money_entries_or_void(self):
        enrollment = self.confirmed(paid='10000')
        second = self.paid_id(enrollment, '2000')
        for role in ('Counselor', 'Front Desk', 'Trainer'):
            self.client.force_authenticate(self.role_user(role))
            self.assertEqual(self.entry(enrollment, 'refund', '100').status_code, 403, role)
            self.assertEqual(self.void(enrollment, second).status_code, 403, role)
        self.client.force_authenticate(self.role_user('Branch Manager'))
        self.assertEqual(self.void(enrollment, second, reason='Entered twice').status_code, 200)
        self.client.force_authenticate(self.role_user('Accountant'))
        self.assertEqual(self.entry(enrollment, 'refund', '100', 'Paid the bank fee for them').status_code, 201)
        self.assertEqual(self.card(enrollment)['remaining_amount'], '20100.00')


class NepalTimeMoneyTests(MoneyFlowBase):
    """Money days, due dates and numbers follow Nepal time."""

    def at(self, moment):
        return mock.patch('django.utils.timezone.now', return_value=moment)

    def test_confirm_just_after_nepal_new_year_uses_the_new_year(self):
        enrollment = self.enroll()
        with self.at(datetime(2026, 12, 31, 19, 0, tzinfo=UTC)):  # 00:45 on 1 Jan 2027 in Nepal
            response = self.confirm(enrollment, paid='5000')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['due_dates'][0]['due_on'], '2027-01-01')
        numbers = sorted(Invoice.objects.filter(enrollment=enrollment).values_list('invoice_number', flat=True))
        self.assertEqual(numbers, ['INV-2027-000001', 'RCT-2027-000001'])

        def collected(day):
            return money(self.report('revenue', start_date=day, end_date=day)['summary']['total_collected'])

        self.assertEqual(collected('2027-01-01'), 5000)
        self.assertEqual(collected('2026-12-31'), 0)

    def test_revenue_days_follow_nepal_time(self):
        enrollment = self.confirmed(paid='1000')
        self.paid_id(enrollment, '2000', when=datetime(2030, 11, 30, 18, 0, tzinfo=UTC))  # 23:45, 30 Nov in Nepal
        self.paid_id(enrollment, '3000', when=datetime(2030, 11, 30, 18, 30, tzinfo=UTC))  # 00:15, 1 Dec in Nepal

        def collected(day):
            return money(self.report('revenue', start_date=day, end_date=day)['summary']['total_collected'])

        self.assertEqual(collected('2030-11-30'), 2000)
        self.assertEqual(collected('2030-12-01'), 3000)

    def test_a_part_is_late_from_nepal_midnight(self):
        enrollment = self.confirmed(paid='5000', due_dates=[{'due_on': '2030-06-01', 'amount': '30000'}])
        with self.at(datetime(2030, 6, 1, 18, 0, tzinfo=UTC)):  # 23:45 on 1 June in Nepal
            before = self.owed_row(enrollment)
        with self.at(datetime(2030, 6, 1, 18, 30, tzinfo=UTC)):  # 00:15 on 2 June in Nepal
            after = self.owed_row(enrollment)
        self.assertEqual((before['days_late'], money(before['late'])), (0, 0))
        self.assertEqual((after['days_late'], money(after['late'])), (1, 25000))


class ReminderFlowTests(MoneyFlowBase):
    """Payment reminders follow payments, voids and write-offs made through the API."""

    def setUp(self):
        super().setUp()
        self.config = ReminderConfig.load()
        self.config.is_active = True
        self.config.save()

    def run_reminders(self, days=0):
        return send_payment_reminders(today=self.today + timedelta(days=days), config=self.config)

    def test_reminders_follow_payments_and_voids(self):
        enrollment = self.confirmed(paid='4000', due_dates=[
            {'due_on': self.day(0), 'amount': '10000'}, {'due_on': self.day(30), 'amount': '20000'},
        ])
        sent = self.run_reminders()
        self.assertEqual(
            [(r['due_on'], r['amount'], r['step']) for r in sent], [(self.today, Decimal('6000'), 'due_today')],
        )
        self.assertIn('NPR 6,000 due today', mail.outbox[-1].body)

        bounced = self.paid_id(enrollment, '6000')
        self.assertEqual(self.run_reminders(1), [])
        self.void(enrollment, bounced, reason='Cheque bounced')
        sent = self.run_reminders(7)
        self.assertEqual([(r['amount'], r['step'], r['days_late']) for r in sent], [(Decimal('6000'), 'late', 7)])

        listed = self.client.get(self.url(enrollment, 'reminders/')).data['results']
        self.assertEqual(sorted(r['step'] for r in listed), ['due_today', 'late'])

    def test_writing_off_the_rest_stops_the_reminders(self):
        enrollment = self.confirmed(paid='5000', due_dates=[{'due_on': self.day(-8), 'amount': '30000'}])
        self.added(enrollment, 'write_off', '25000', 'Student cannot pay')
        self.assertEqual(self.run_reminders(), [])
        self.assertIsNone(self.owed_row(enrollment))


class InvoiceFlowTests(MoneyFlowBase):
    """Fee invoices and receipts across cancels, voids and money entries."""

    def test_a_new_fee_invoice_after_a_cancel_shows_the_money_as_it_is_now(self):
        enrollment = self.confirmed(paid='5000')
        self.paid_id(enrollment, '5000')
        self.added(enrollment, 'extra_charge', '2000', 'Exam fee')
        self.cancel_invoice(Invoice.objects.get(enrollment=enrollment, type='enrollment'))
        self.assertFalse(self.card(enrollment)['has_fee_invoice'])

        response = self.client.post(self.url(enrollment, 'fee-invoice/'))
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(
            (response.data['total_paid_so_far'], response.data['remaining_balance']), ('10000.00', '22000.00'),
        )
        self.assertTrue(self.card(enrollment)['has_fee_invoice'])
        self.assertEqual(self.history(enrollment)[0]['action'], 'cancel_invoice')

    def test_no_fee_invoice_or_due_dates_for_a_cancelled_enrollment(self):
        enrollment = self.confirmed()
        self.cancel_invoice(Invoice.objects.get(enrollment=enrollment, type='enrollment'))
        self.client.post(self.url(enrollment, 'cancel/'), {'reason': 'Moved abroad'}, format='json')

        response = self.client.post(self.url(enrollment, 'fee-invoice/'))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['detail'], 'A fee invoice is made only for a confirmed enrollment.')
        response = self.due_dates(enrollment, [{'due_on': self.day(5), 'amount': '30000'}])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['detail'], 'Due dates can be set only on a confirmed enrollment.')
        self.assertFalse(Invoice.objects.filter(enrollment=enrollment, type='enrollment', status='active').exists())

    def test_voiding_a_payment_cancels_the_receipt_made_again(self):
        enrollment = self.confirmed(paid='5000')
        payment_id = self.paid_id(enrollment, '3000')
        self.cancel_invoice(Invoice.objects.get(payment_id=payment_id), reason='Wrong method')
        again = self.client.post(self.url(enrollment, f'payments/{payment_id}/receipt/'))
        self.assertEqual(again.status_code, 201, again.data)

        self.assertEqual(self.void(enrollment, payment_id).status_code, 200)
        self.assertFalse(Invoice.objects.filter(payment_id=payment_id, status='active').exists())
        self.assertEqual(Invoice.objects.filter(payment_id=payment_id).count(), 2)
        listed = self.client.get(self.url(enrollment, 'payments/')).data['results']
        row = next(p for p in listed if p['id'] == payment_id)
        self.assertEqual((row['status'], row['receipt_number']), ('voided', None))
        actions = [r['action'] for r in self.history(enrollment)]
        self.assertEqual(actions[:2], ['cancel_invoice', 'void_payment'])
        self.assertEqual(actions.count('cancel_invoice'), 2)


class PaymentAmountTests(MoneyFlowBase):
    """Typo checks on amounts, across money entries and pending payments."""

    def test_bad_amounts_are_refused_and_the_smallest_is_taken(self):
        enrollment = self.confirmed(paid='5000')
        for bad in ('0.001', '1000.555', 'NaN', 'Infinity', '-0.01', '10000000000', ''):
            response = self.pay(enrollment, bad)
            self.assertEqual(response.status_code, 400, bad)
            self.assertIn('amount', response.data, bad)
        self.assertEqual(self.pay(enrollment, '0.01').status_code, 201)
        self.assertEqual(Payment.objects.filter(enrollment=enrollment).count(), 2)

    def test_a_first_payment_with_too_many_decimals_keeps_the_enrollment_pending(self):
        enrollment = self.enroll()
        response = self.confirm(enrollment, paid='5000.005')
        self.assertEqual(response.status_code, 400)
        self.assertIn('first_payment', response.data)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.status, 'pending')
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(Invoice.objects.exists())
        self.assertFalse(Student.objects.exists())

    def test_paying_to_the_last_paisa_after_an_extra_charge(self):
        enrollment = self.confirmed(paid='10000')
        self.added(enrollment, 'extra_charge', '0.50', 'Bank charge')
        self.assertEqual(self.pay(enrollment, '20000.51').status_code, 400)
        self.assertEqual(self.pay(enrollment, '20000.50').status_code, 201)
        self.assertEqual(self.pay(enrollment, '0.01').data['amount'], ['Nothing is left to pay on this enrollment.'])
        card = self.card(enrollment)
        self.assertEqual((card['remaining_amount'], card['is_full_paid']), ('0.00', True))
        self.assertIsNone(self.owed_row(enrollment))

    def test_what_is_left_counts_entries_and_money_on_its_way(self):
        enrollment = self.confirmed(paid='10000')
        self.added(enrollment, 'discount', '5000', 'Sibling offer')
        pending = self.paid_id(enrollment, '10000', status='pending')
        self.assertEqual(
            self.pay(enrollment, '5000.01').data['amount'],
            ['This is more than what is left to pay (NPR 5,000). Check the amount.'],
        )
        self.paid_id(enrollment, '5000')
        response = self.client.post(self.url(enrollment, f'payments/{pending}/confirm/'))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.card(enrollment)['remaining_amount'], '0.00')

    def test_a_payment_after_a_discount_left_a_credit_is_refused(self):
        enrollment = self.confirmed(paid='25000')
        self.added(enrollment, 'discount', '10000', 'Scholarship')
        self.assertEqual(self.card(enrollment)['remaining_amount'], '-5000.00')
        self.assertEqual(self.pay(enrollment, '1').data['amount'], ['Nothing is left to pay on this enrollment.'])
        self.assertIsNone(self.owed_row(enrollment))
        self.assertEqual(money(self.report('revenue')['summary']['outstanding_to_collect']), 0)

    # BUG: an extra charge has no cap, and a 13-digit balance overflows the 12-digit remaining_amount field (lead/serializers.py:262): 500.
    @unittest.expectedFailure
    def test_a_huge_extra_charge_does_not_break_the_enrollment_screens(self):
        enrollment = self.confirmed(paid='5000')
        self.entry(enrollment, 'extra_charge', '9999999999.99', 'Typed too many nines')
        self.client.raise_request_exception = False
        self.assertEqual(self.client.get(self.url(enrollment)).status_code, 200)
        self.assertEqual(self.client.get('/api/enrollments/').status_code, 200)


class MoneyEntryFlowTests(MoneyFlowBase):
    """Money entries on cancelled and completed enrollments."""

    def test_refund_after_cancel_gives_back_only_what_was_paid(self):
        enrollment = self.confirmed(paid='10000', batch=self.batch)
        self.client.post(self.url(enrollment, 'cancel/'), {'reason': 'Cancelled before the start'}, format='json')
        self.added(enrollment, 'refund', '6000', 'Cancelled before the start')
        again = self.entry(enrollment, 'refund', '6000', 'Clicked twice')
        self.assertEqual(again.status_code, 400)
        self.assertIn('Only 4000.00 can be refunded', again.data['detail'])
        self.assertEqual(self.card(enrollment)['refunds_total'], '6000.00')
        self.assertIsNone(self.owed_row(enrollment))
        batch = self.batch_row(self.batch)
        self.assertEqual((batch['students'], money(batch['collected'])), (0, 4000))
        refund = next(r for r in self.history(enrollment) if r['action'] == 'refund')
        self.assertEqual((refund['before']['balance'], refund['after']['balance']), ('20000.00', '26000.00'))

    def test_a_completed_enrollment_can_still_be_paid_off(self):
        enrollment = self.confirmed(paid='10000')
        response = self.client.post(
            self.url(enrollment, 'status/'), {'status': 'completed', 'reason': 'Course over'}, format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.owed_row(enrollment)['owed'], '20000.00')
        self.paid_id(enrollment, '20000')
        self.assertIsNone(self.owed_row(enrollment))
        self.assertEqual(self.card(enrollment)['remaining_amount'], '0.00')


class PendingEnrollmentMoneyTests(MoneyFlowBase):
    """Money on an enrollment before it is confirmed."""

    def test_a_full_batch_stops_the_confirm_and_keeps_no_money(self):
        small = CourseBatch.objects.create(
            name='Python Small', course=self.course, capacity=1, status='running', branch=self.ktm,
        )
        first, second = self.enroll(batch=small), self.enroll(batch=small)
        self.assertEqual(self.confirm(first).status_code, 200)
        response = self.confirm(second, paid='5000')
        self.assertEqual(response.status_code, 400)
        self.assertIn('full', response.data['detail'])
        second.refresh_from_db()
        self.assertEqual(second.status, 'pending')
        self.assertFalse(Payment.objects.filter(enrollment=second).exists())
        self.assertFalse(Invoice.objects.filter(enrollment=second).exists())
        self.assertFalse(Student.objects.filter(lead=second.lead).exists())

    # BUG: confirm wants something paid when agreed > 0 (lead/services.py:637), but a full discount entry leaves nothing to pay: stuck.
    @unittest.expectedFailure
    def test_a_fully_discounted_pending_enrollment_is_not_stuck(self):
        enrollment = self.enroll()
        self.entry(enrollment, 'discount', '30000', 'Full scholarship')
        without_payment = self.confirm(enrollment, paid=None)
        with_payment = self.confirm(enrollment, paid='1')
        self.assertIn(200, (without_payment.status_code, with_payment.status_code))


class DueDateFlowTests(MoneyFlowBase):
    """Due dates changed after money has moved."""

    def test_new_due_dates_after_payments_are_filled_oldest_first(self):
        enrollment = self.confirmed(paid='15000')
        response = self.due_dates(enrollment, [
            {'due_on': self.day(40), 'amount': '25000'}, {'due_on': self.day(10), 'amount': '5000'},
        ])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            [(r['due_on'], r['paid'], r['left'], r['status']) for r in response.data['due_dates']],
            [(self.day(10), '5000.00', '0.00', 'paid'), (self.day(40), '10000.00', '15000.00', 'part_paid')],
        )
        row = self.owed_row(enrollment)
        self.assertEqual(
            (row['owed'], money(row['late']), row['next_due_on'], row['next_due_amount']),
            ('15000.00', 0, self.day(40), '15000.00'),
        )

    def test_due_dates_split_the_agreed_amount_and_extra_charges_stay_apart(self):
        enrollment = self.confirmed(paid='5000')
        self.added(enrollment, 'extra_charge', '2000', 'Exam fee')
        self.added(enrollment, 'discount', '1000', 'Promo')
        refused = self.due_dates(enrollment, [{'due_on': self.day(30), 'amount': '31000'}])
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(refused.data['detail'], 'The due dates add up to 31000.00, but the agreed amount is 30000.00.')

        response = self.due_dates(enrollment, [{'due_on': self.day(30), 'amount': '30000'}])
        self.assertEqual(response.status_code, 200, response.data)
        rows = response.data['due_dates']
        self.assertEqual([r['kind'] for r in rows], ['extra_charge', 'part'])
        self.assertEqual(sum(money(r['left']) for r in rows), money(response.data['remaining_amount']))
        self.assertEqual(money(response.data['remaining_amount']), 26000)

    def test_bad_due_date_parts_are_refused(self):
        enrollment = self.confirmed()
        for parts in (
            [{'due_on': self.day(5), 'amount': '30000.005'}],
            [{'due_on': self.day(5), 'amount': '0'}, {'due_on': self.day(6), 'amount': '30000'}],
            [{'due_on': self.day(5), 'amount': '-1'}, {'due_on': self.day(6), 'amount': '30001'}],
            [{'due_on': 'not-a-date', 'amount': '30000'}],
            [],
        ):
            self.assertEqual(self.due_dates(enrollment, parts).status_code, 400, parts)
        self.assertEqual(PaymentPlan.objects.filter(enrollment=enrollment).count(), 1)


class HistoryTests(MoneyFlowBase):
    """Every money action leaves a history row, newest first, with the balance."""

    def test_money_actions_are_recorded_in_order_with_the_balance(self):
        enrollment = self.confirmed(paid='10000')
        self.added(enrollment, 'extra_charge', '2000', 'Exam fee')
        self.added(enrollment, 'discount', '3000', 'Festival offer')
        paid = self.paid_id(enrollment, '4000')
        self.added(enrollment, 'write_off', '1000', 'Manager approved')
        self.void(enrollment, paid, reason='Cheque bounced')
        self.added(enrollment, 'refund', '2000', 'Returned the exam fee')
        self.assertEqual(
            self.due_dates(enrollment, [{'due_on': self.day(10), 'amount': '30000'}], 'Pays after Dashain').status_code,
            200,
        )
        self.cancel_invoice(Invoice.objects.get(enrollment=enrollment, type='enrollment'), reason='Wrong address')

        rows = self.history(enrollment)
        self.assertEqual([r['action'] for r in rows], [
            'cancel_invoice', 'change_due_dates', 'refund', 'cancel_invoice', 'void_payment',
            'write_off', 'discount', 'extra_charge', 'confirm',
        ])
        balances = {
            r['action']: (r['before']['balance'], r['after']['balance'])
            for r in rows if r['action'] in ('extra_charge', 'discount', 'write_off', 'refund')
        }
        self.assertEqual(balances, {
            'extra_charge': ('20000.00', '22000.00'),
            'discount': ('22000.00', '19000.00'),
            'write_off': ('15000.00', '14000.00'),
            'refund': ('18000.00', '20000.00'),
        })
        void = next(r for r in rows if r['action'] == 'void_payment')
        self.assertEqual(
            (void['reason'], void['before']['amount'], void['after']['status']), ('Cheque bounced', '4000.00', 'voided'),
        )
        self.assertEqual(self.card(enrollment)['remaining_amount'], '20000.00')
