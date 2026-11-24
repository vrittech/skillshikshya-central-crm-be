from datetime import date, timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.db.models import ProtectedError
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.course.models import Course, CourseBatch
from apps.invoices.models import Invoice
from apps.lead.models import (
    Adjustment, Lead, Enrollment, Payment, EnrollmentHistory, Placement, PaymentPlan,
    Installment, FollowUp, ReminderConfig, ReminderLog, TeamsLicence, LeadCourseInterest,
    EnquiryStageChange, LeadSource,
)
from apps.lead import services
from apps.lead.seat_backfill import backfill_seats
from apps.student.models import Student

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


class MoneyCannotBeDeletedTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email='money@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        course = Course.objects.create(name='Python', full_amount=30000)
        self.lead = Lead.objects.create(full_name='Sita', phone='9800000100')
        self.enrollment = Enrollment.objects.create(
            lead=self.lead, course=course, enrollment_date=date.today(),
            listed_fee=30000, agreed_amount=30000,
        )
        self.payment = Payment.objects.create(
            enrollment=self.enrollment, amount=10000,
            payment_date=timezone.now(), payment_method='cash',
        )

    def test_payment_delete_is_blocked(self):
        url = f'/api/leads/{self.lead.id}/enrollments/{self.enrollment.id}/payments/{self.payment.id}/'
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertTrue(Payment.objects.filter(id=self.payment.id).exists())

    def test_enrollment_delete_is_blocked(self):
        url = f'/api/leads/{self.lead.id}/enrollments/{self.enrollment.id}/'
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertTrue(Enrollment.objects.filter(id=self.enrollment.id).exists())


class EnrollmentHistoryTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email='history@test.com', name='Admin', password='pass', is_staff=True,
        )
        course = Course.objects.create(name='Python', full_amount=30000)
        self.lead = Lead.objects.create(full_name='Ram', phone='9800000200')
        self.enrollment = Enrollment.objects.create(
            lead=self.lead, course=course, enrollment_date=date.today(),
            listed_fee=30000, agreed_amount=30000,
        )
        self.client.force_authenticate(self.admin)
        self.history_url = (
            f'/api/leads/{self.lead.id}/enrollments/{self.enrollment.id}/history/'
        )

    def test_change_is_recorded_with_who_what_why(self):
        row = EnrollmentHistory.record(
            enrollment=self.enrollment,
            action=EnrollmentHistory.Action.DISCOUNT,
            reason='Scholarship for top student',
            performed_by=self.admin,
            before={'agreed_amount': '30000.00'},
            after={'agreed_amount': '25000.00'},
        )
        self.assertEqual(row.enrollment, self.enrollment)
        self.assertEqual(row.action, 'discount')
        self.assertEqual(row.reason, 'Scholarship for top student')
        self.assertEqual(row.performed_by, self.admin)
        self.assertEqual(row.before, {'agreed_amount': '30000.00'})
        self.assertEqual(row.after, {'agreed_amount': '25000.00'})
        self.assertIsNotNone(row.created_at)

    def test_reason_is_required(self):
        with self.assertRaises(ValidationError):
            EnrollmentHistory.record(
                enrollment=self.enrollment,
                action=EnrollmentHistory.Action.CANCEL,
                reason='   ',
                performed_by=self.admin,
            )
        self.assertEqual(EnrollmentHistory.objects.count(), 0)

    def test_history_row_cannot_be_changed_or_deleted(self):
        row = EnrollmentHistory.record(
            enrollment=self.enrollment,
            action=EnrollmentHistory.Action.CANCEL,
            reason='Student moved abroad',
            performed_by=self.admin,
        )
        row.reason = 'Something else'
        with self.assertRaises(ValidationError):
            row.save()
        with self.assertRaises(ValidationError):
            row.delete()
        saved = EnrollmentHistory.objects.get(id=row.id)
        self.assertEqual(saved.reason, 'Student moved abroad')

    def test_history_api_lists_changes_newest_first(self):
        paused = EnrollmentHistory.record(
            enrollment=self.enrollment,
            action=EnrollmentHistory.Action.PAUSE,
            reason='Exams next month',
            performed_by=self.admin,
        )
        resumed = EnrollmentHistory.record(
            enrollment=self.enrollment,
            action=EnrollmentHistory.Action.RESUME,
            reason='Exams finished',
            performed_by=self.admin,
        )
        response = self.client.get(self.history_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [r['id'] for r in response.data['results']]
        self.assertEqual(ids, [resumed.id, paused.id])
        row = response.data['results'][0]
        self.assertEqual(row['action_display'], 'Resume')
        self.assertEqual(row['reason'], 'Exams finished')
        self.assertEqual(row['performed_by_name'], 'Admin')

    def test_history_api_is_read_only(self):
        row = EnrollmentHistory.record(
            enrollment=self.enrollment,
            action=EnrollmentHistory.Action.CANCEL,
            reason='Student moved abroad',
            performed_by=self.admin,
        )
        post = self.client.post(
            self.history_url, {'action': 'cancel', 'reason': 'x'}, format='json',
        )
        self.assertEqual(post.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        patch = self.client.patch(
            f'{self.history_url}{row.id}/', {'reason': 'changed'}, format='json',
        )
        self.assertEqual(patch.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        delete = self.client.delete(f'{self.history_url}{row.id}/')
        self.assertEqual(delete.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(EnrollmentHistory.objects.count(), 1)


class EnrollmentCancelTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email='cancel@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        course = Course.objects.create(name='Python', full_amount=30000)
        self.lead = Lead.objects.create(full_name='Hari', phone='9800000300')
        self.enrollment = Enrollment.objects.create(
            lead=self.lead, course=course, enrollment_date=date.today(),
            listed_fee=30000, agreed_amount=30000, status='active',
        )
        self.enrollment_url = f'/api/leads/{self.lead.id}/enrollments/{self.enrollment.id}/'
        self.cancel_url = f'{self.enrollment_url}cancel/'

    def test_cancel_with_reason(self):
        response = self.client.post(
            self.cancel_url, {'reason': 'Moved abroad'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'cancelled')
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, 'cancelled')
        self.assertEqual(self.enrollment.updated_by, self.admin)
        row = self.enrollment.history.get()
        self.assertEqual(row.action, 'cancel')
        self.assertEqual(row.reason, 'Moved abroad')
        self.assertEqual(row.before, {'status': 'active'})
        self.assertEqual(row.after, {'status': 'cancelled'})
        self.assertEqual(row.performed_by, self.admin)

    def test_cancel_without_reason_is_refused(self):
        for body in ({}, {'reason': '   '}):
            response = self.client.post(self.cancel_url, body, format='json')
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn('reason', response.data)
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, 'active')
        self.assertFalse(self.enrollment.history.exists())

    def test_cancel_twice_is_refused(self):
        self.client.post(self.cancel_url, {'reason': 'Moved abroad'}, format='json')
        response = self.client.post(self.cancel_url, {'reason': 'Again'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('already cancelled', response.data['detail'])
        self.assertEqual(self.enrollment.history.count(), 1)

    def test_patch_cannot_cancel_without_reason(self):
        response = self.client.patch(
            self.enrollment_url, {'status': 'cancelled'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('status', response.data)
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, 'active')

    def test_reinstate_restores_old_status_with_reason(self):
        self.client.post(self.cancel_url, {'reason': 'Moved abroad'}, format='json')
        response = self.client.post(
            f'{self.enrollment_url}reinstate/', {'reason': 'Came back'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, 'active')
        row = self.enrollment.history.order_by('-id').first()
        self.assertEqual(row.action, 'reinstate')
        self.assertEqual(row.reason, 'Came back')
        self.assertEqual(row.after, {'status': 'active'})

    def test_reinstate_needs_a_cancelled_enrollment(self):
        response = self.client.post(
            f'{self.enrollment_url}reinstate/', {'reason': 'Came back'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(self.enrollment.history.exists())

    def test_patch_cannot_reopen_a_cancelled_enrollment(self):
        self.client.post(self.cancel_url, {'reason': 'Moved abroad'}, format='json')
        response = self.client.patch(
            self.enrollment_url, {'status': 'active'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        # Other fields of a cancelled enrollment can still be saved.
        ok = self.client.patch(
            self.enrollment_url, {'status': 'cancelled', 'remarks': 'Note'}, format='json',
        )
        self.assertEqual(ok.status_code, status.HTTP_200_OK)


class PaymentActionTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email='void@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        course = Course.objects.create(name='Python', full_amount=30000)
        self.lead = Lead.objects.create(full_name='Gita', phone='9800000400')
        self.enrollment = Enrollment.objects.create(
            lead=self.lead, course=course, enrollment_date=date.today(),
            listed_fee=30000, agreed_amount=30000, status='active',
        )
        self.payments_url = (
            f'/api/leads/{self.lead.id}/enrollments/{self.enrollment.id}/payments/'
        )

    def _pay(self, amount='10000', pay_status='successful'):
        return self.client.post(self.payments_url, {
            'amount': amount,
            'payment_date': timezone.now().isoformat(),
            'payment_method': 'cash',
            'status': pay_status,
        }, format='json')

    def test_new_payment_must_be_pending_or_successful(self):
        for bad in ('voided', 'refunded', 'failed'):
            response = self._pay(pay_status=bad)
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn('status', response.data)
        self.assertFalse(Payment.objects.exists())

    def test_amount_must_be_more_than_zero(self):
        for bad in ('0', '-500'):
            response = self._pay(amount=bad)
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn('amount', response.data)

    def test_void_with_reason(self):
        payment_id = self._pay().data['id']
        response = self.client.post(
            f'{self.payments_url}{payment_id}/void/',
            {'reason': 'Typed 10000 instead of 1000'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'voided')
        payment = Payment.objects.get(id=payment_id)
        self.assertEqual(payment.void_reason, 'Typed 10000 instead of 1000')
        self.assertEqual(payment.voided_by, self.admin)
        self.assertIsNotNone(payment.voided_at)
        # A voided payment no longer counts as paid.
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.total_paid, 0)
        row = self.enrollment.history.get(action='void_payment')
        self.assertEqual(row.before['status'], 'successful')
        self.assertEqual(row.after['status'], 'voided')

    def test_void_needs_a_reason(self):
        payment_id = self._pay().data['id']
        response = self.client.post(
            f'{self.payments_url}{payment_id}/void/', {'reason': ' '}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Payment.objects.get(id=payment_id).status, 'successful')
        self.assertFalse(self.enrollment.history.exists())

    def test_void_twice_is_refused(self):
        payment_id = self._pay().data['id']
        url = f'{self.payments_url}{payment_id}/void/'
        self.client.post(url, {'reason': 'Duplicate entry'}, format='json')
        again = self.client.post(url, {'reason': 'Again'}, format='json')
        self.assertEqual(again.status_code, status.HTTP_400_BAD_REQUEST)
        # One row for the void, one for its receipt; the refused try adds none.
        self.assertEqual(
            sorted(self.enrollment.history.values_list('action', flat=True)),
            ['cancel_invoice', 'void_payment'],
        )

    def test_confirm_only_a_pending_payment(self):
        payment_id = self._pay(pay_status='pending').data['id']
        url = f'{self.payments_url}{payment_id}/confirm/'
        ok = self.client.post(url, {}, format='json')
        self.assertEqual(ok.status_code, status.HTTP_200_OK)
        self.assertEqual(ok.data['status'], 'successful')
        again = self.client.post(url, {}, format='json')
        self.assertEqual(again.status_code, status.HTTP_400_BAD_REQUEST)

    def test_payment_cannot_be_patched(self):
        payment_id = self._pay().data['id']
        response = self.client.patch(
            f'{self.payments_url}{payment_id}/', {'amount': '1'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


class AdjustmentTests(APITestCase):
    """Extra charge, discount, write-off and refund: added with a reason, never edited."""

    def setUp(self):
        self.admin = User.objects.create_user(
            email='adjust@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        course = Course.objects.create(name='Python', full_amount=30000)
        self.lead = Lead.objects.create(full_name='Maya', phone='9800000500')
        self.enrollment = Enrollment.objects.create(
            lead=self.lead, course=course, enrollment_date=date.today(),
            listed_fee=30000, agreed_amount=30000, status='active',
        )
        self.enrollment_url = f'/api/leads/{self.lead.id}/enrollments/{self.enrollment.id}/'
        self.url = f'{self.enrollment_url}adjustments/'

    def _add(self, kind, amount, reason='Agreed with the manager'):
        return self.client.post(
            self.url, {'kind': kind, 'amount': amount, 'reason': reason}, format='json',
        )

    def _pay(self, amount):
        Payment.objects.create(
            enrollment=self.enrollment, amount=amount, payment_date=timezone.now(),
            payment_method='cash', status='successful',
        )

    def _owes(self):
        return self.client.get(self.enrollment_url).data['remaining_amount']

    def test_extra_charge_raises_what_is_owed(self):
        response = self._add('extra_charge', '2000', 'Exam fee')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['kind_display'], 'Extra charge')
        self.assertEqual(self._owes(), '32000.00')
        row = self.enrollment.history.get()
        self.assertEqual(row.action, 'extra_charge')
        self.assertEqual(row.before['balance'], '30000.00')
        self.assertEqual(row.after['balance'], '32000.00')

    def test_switch_to_cheaper_course_discount_then_refund(self):
        # Paid in full, then moved to a course that costs 10000 less.
        self._pay(30000)
        self.assertEqual(self._add('discount', '10000').status_code, 201)
        self.assertEqual(self._owes(), '-10000.00')  # we owe the student
        self.assertEqual(self._add('refund', '10000').status_code, 201)
        self.assertEqual(self._owes(), '0.00')
        data = self.client.get(self.enrollment_url).data
        self.assertEqual(data['discounts_total'], '10000.00')
        self.assertEqual(data['refunds_total'], '10000.00')
        self.assertEqual(self.enrollment.history.count(), 2)

    def test_refund_cannot_be_more_than_paid(self):
        self._pay(5000)
        response = self._add('refund', '6000')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('5000', response.data['detail'])
        self.assertFalse(Adjustment.objects.exists())

    def test_write_off_cannot_be_more_than_owed(self):
        self._pay(25000)
        self.assertEqual(self._add('write_off', '6000').status_code, 400)
        self.assertEqual(self._add('write_off', '5000').status_code, 201)
        self.assertEqual(self._owes(), '0.00')

    def test_reason_amount_and_kind_are_checked(self):
        self.assertIn('reason', self._add('discount', '100', reason='  ').data)
        self.assertIn('amount', self._add('discount', '0').data)
        self.assertIn('kind', self._add('bonus', '100').data)
        self.assertFalse(Adjustment.objects.exists())
        self.assertFalse(self.enrollment.history.exists())

    def test_money_entries_cannot_be_changed_or_deleted(self):
        entry_id = self._add('discount', '1000').data['id']
        self.assertEqual(
            self.client.patch(f'{self.url}{entry_id}/', {'amount': '1'}, format='json').status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        self.assertEqual(
            self.client.delete(f'{self.url}{entry_id}/').status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        entry = Adjustment.objects.get(id=entry_id)
        from django.core.exceptions import ValidationError as ModelValidationError
        with self.assertRaises(ModelValidationError):
            entry.delete()
        self.assertEqual(self.client.get(self.url).data['count'], 1)

    def test_price_cannot_be_edited_once_confirmed(self):
        response = self.client.patch(
            self.enrollment_url, {'agreed_amount': '25000'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('agreed_amount', response.data)
        # Sending the same price back (as the edit form does) is fine.
        same = self.client.patch(
            self.enrollment_url,
            {'agreed_amount': '30000.00', 'listed_fee': '30000', 'remarks': 'ok'},
            format='json',
        )
        self.assertEqual(same.status_code, status.HTTP_200_OK)


class MoneyIsNeverCascadedTests(APITestCase):
    """A lead, course or batch that has enrollments cannot be deleted."""

    def setUp(self):
        self.admin = User.objects.create_user(
            email='protect@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.course = Course.objects.create(name='Python', full_amount=30000)
        self.batch = CourseBatch.objects.create(name='Morning', course=self.course)
        self.lead = Lead.objects.create(full_name='Hari', phone='9800000700')
        self.enrollment = Enrollment.objects.create(
            lead=self.lead, course=self.course, batch=self.batch,
            enrollment_date=date.today(), listed_fee=30000, agreed_amount=30000,
        )
        Payment.objects.create(
            enrollment=self.enrollment, amount=5000,
            payment_date=timezone.now(), payment_method='cash',
        )

    def test_purge_refuses_a_lead_with_enrollments(self):
        response = self.client.post(f'/api/leads/{self.lead.id}/purge/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('money records', response.data['detail'])
        self.assertTrue(Lead._base_manager.filter(id=self.lead.id).exists())
        self.assertEqual(Payment.objects.count(), 1)

    def test_purge_still_works_without_enrollments(self):
        lead = Lead.objects.create(full_name='Empty', phone='9800000701')
        response = self.client.post(f'/api/leads/{lead.id}/purge/')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Lead._base_manager.filter(id=lead.id).exists())

    def test_course_with_enrollments_cannot_be_deleted(self):
        response = self.client.delete(f'/api/courses/{self.course.id}/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('enrollments', response.data['detail'])
        self.assertTrue(Course.objects.filter(id=self.course.id).exists())
        self.assertEqual(Payment.objects.count(), 1)

    def test_batch_with_enrollments_cannot_be_deleted(self):
        response = self.client.delete(f'/api/course-batches/{self.batch.id}/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.batch_id, self.batch.id)

    def test_orm_delete_is_refused_too(self):
        with self.assertRaises(ProtectedError):
            self.lead.delete()
        with self.assertRaises(ProtectedError):
            Enrollment.objects.filter(id=self.enrollment.id).delete()
        self.assertEqual(Payment.objects.count(), 1)

    def test_course_without_enrollments_can_still_be_deleted(self):
        course = Course.objects.create(name='Unused', full_amount=1000)
        response = self.client.delete(f'/api/courses/{course.id}/')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)


class LeadActionRoutingTests(APITestCase):
    """Archive, recycle bin, export and purge live on /api/leads/, not /api/notifications/."""

    def setUp(self):
        self.admin = User.objects.create_user(
            email='routes@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.lead = Lead.objects.create(full_name='Gita', phone='9800000800')

    def test_archive_and_restore(self):
        self.assertEqual(self.client.post(f'/api/leads/{self.lead.id}/archive/').status_code, 200)
        self.lead.refresh_from_db()
        self.assertTrue(self.lead.is_archived)
        self.assertEqual(self.client.post(f'/api/leads/{self.lead.id}/restore/').status_code, 200)
        self.lead.refresh_from_db()
        self.assertFalse(self.lead.is_archived)

    def test_recycle_bin_lists_deleted_leads(self):
        self.client.delete(f'/api/leads/{self.lead.id}/')
        response = self.client.get('/api/leads/recycle_bin/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r['id'] for r in response.data['results']], [self.lead.id])

    def test_restore_brings_a_deleted_lead_back(self):
        self.client.delete(f'/api/leads/{self.lead.id}/')
        response = self.client.post(f'/api/leads/{self.lead.id}/restore/')
        self.assertEqual(response.status_code, 200)
        self.lead.refresh_from_db()
        self.assertFalse(self.lead.is_deleted)
        self.assertIsNone(self.lead.deleted_at)
        self.assertIsNone(self.lead.deleted_by)
        listed = [r['id'] for r in self.client.get('/api/leads/').data['results']]
        self.assertIn(self.lead.id, listed)
        self.assertEqual(self.client.get('/api/leads/recycle_bin/').data['results'], [])

    def test_restore_of_a_missing_lead_is_404(self):
        response = self.client.post('/api/leads/999999/restore/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_export_csv(self):
        response = self.client.get('/api/leads/export_csv/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Gita', response.content.decode())

    def test_notifications_url_cannot_purge_a_lead(self):
        response = self.client.post(f'/api/notifications/{self.lead.id}/purge/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(Lead._base_manager.filter(id=self.lead.id).exists())


class ConfirmEnrollmentTests(APITestCase):
    """A lead becomes a student when an enrollment is confirmed, not on payment."""

    def setUp(self):
        self.admin = User.objects.create_user(
            email='confirm@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.course = Course.objects.create(name='Python', full_amount=30000)
        self.lead = Lead.objects.create(
            full_name='Sita Rai', phone='9800000900', email='sita@example.com',
        )
        self.enrollments_url = f'/api/leads/{self.lead.id}/enrollments/'
        self.enrollment = self._enroll()

    def _enroll(self, **extra):
        response = self.client.post(self.enrollments_url, {
            'course': self.course.id, 'enrollment_date': str(date.today()),
            'listed_fee': '30000', 'agreed_amount': '30000', **extra,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        return Enrollment.objects.get(id=response.data['id'])

    def _confirm(self, enrollment):
        return self.client.post(f'{self.enrollments_url}{enrollment.id}/confirm/')

    def test_new_enrollment_is_pending_and_not_a_student(self):
        self.assertEqual(self.enrollment.status, 'pending')
        self.assertFalse(Student.objects.exists())
        self.lead.refresh_from_db()
        self.assertFalse(self.lead.is_converted)

    def test_confirm_makes_the_lead_a_student(self):
        response = self._confirm(self.enrollment)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'confirmed')
        student = Student.objects.get()
        self.assertEqual((student.full_name, student.phone, student.email),
                         ('Sita Rai', '9800000900', 'sita@example.com'))
        self.assertEqual(student.lead, self.lead)
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.student, student)
        self.lead.refresh_from_db()
        self.assertTrue(self.lead.is_converted)
        self.assertIsNotNone(self.lead.converted_at)
        row = self.enrollment.history.get()
        self.assertEqual(row.action, 'confirm')
        self.assertEqual(row.after, {
            'status': 'confirmed', 'student': student.student_id, 'batch': None,
            'due_dates': [{'due_on': str(date.today()), 'amount': str(self.enrollment.agreed_amount)}],
        })

    def test_confirm_makes_the_fee_invoice_once(self):
        self._confirm(self.enrollment)
        again = self._confirm(self.enrollment)
        self.assertEqual(again.status_code, status.HTTP_400_BAD_REQUEST)
        invoice = Invoice.objects.get(type='enrollment')
        self.assertEqual(invoice.amount, Decimal('30000'))
        self.assertEqual(invoice.student, Student.objects.get())

    def test_second_course_uses_the_same_student(self):
        self._confirm(self.enrollment)
        second = self._enroll()
        self._confirm(second)
        second.refresh_from_db()
        self.assertEqual(Student.objects.count(), 1)
        self.assertEqual(second.student, Student.objects.get())
        self.assertEqual(Invoice.objects.filter(type='enrollment').count(), 2)

    def test_payment_does_not_make_a_student(self):
        self.client.post(f'{self.enrollments_url}{self.enrollment.id}/payments/', {
            'amount': '5000', 'payment_date': timezone.now().isoformat(),
            'payment_method': 'cash', 'status': 'successful',
        }, format='json')
        self.assertFalse(Student.objects.exists())

    def test_cancelled_enrollment_cannot_be_confirmed(self):
        self.client.post(
            f'{self.enrollments_url}{self.enrollment.id}/cancel/', {'reason': 'No'}, format='json',
        )
        self.assertEqual(self._confirm(self.enrollment).status_code, 400)
        self.assertFalse(Student.objects.exists())

    def test_new_enrollment_cannot_skip_pending(self):
        response = self.client.post(self.enrollments_url, {
            'course': self.course.id, 'enrollment_date': str(date.today()),
            'listed_fee': '30000', 'agreed_amount': '30000', 'status': 'active',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('status', response.data)

    def test_edit_cannot_confirm_or_go_back_to_pending(self):
        url = f'{self.enrollments_url}{self.enrollment.id}/'
        skip = self.client.patch(url, {'status': 'active'}, format='json')
        self.assertEqual(skip.status_code, status.HTTP_400_BAD_REQUEST)
        self._confirm(self.enrollment)
        back = self.client.patch(url, {'status': 'pending'}, format='json')
        self.assertEqual(back.status_code, status.HTTP_400_BAD_REQUEST)
        onward = self.client.patch(url, {'status': 'active'}, format='json')
        self.assertEqual(onward.status_code, status.HTTP_200_OK)

    def test_price_can_change_only_while_pending_and_unpaid(self):
        url = f'{self.enrollments_url}{self.enrollment.id}/'
        ok = self.client.patch(url, {'listed_fee': '32000', 'agreed_amount': '32000'}, format='json')
        self.assertEqual(ok.status_code, status.HTTP_200_OK)
        Payment.objects.create(
            enrollment=self.enrollment, amount=1000, payment_date=timezone.now(),
            payment_method='cash', status='successful',
        )
        paid = self.client.patch(url, {'agreed_amount': '31000'}, format='json')
        self.assertEqual(paid.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('agreed_amount', paid.data)

    def test_price_is_fixed_once_confirmed(self):
        self._confirm(self.enrollment)
        response = self.client.patch(
            f'{self.enrollments_url}{self.enrollment.id}/', {'listed_fee': '1'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_student_link_cannot_be_set_by_hand(self):
        other = Student.objects.create(full_name='Other', phone='1')
        self.client.patch(
            f'{self.enrollments_url}{self.enrollment.id}/', {'student': other.id}, format='json',
        )
        self.enrollment.refresh_from_db()
        self.assertIsNone(self.enrollment.student)

    def test_confirm_needs_permission(self):
        counselor = User.objects.create_user(email='c9@t.com', name='C9', password='x')
        self.client.force_authenticate(counselor)
        self.assertEqual(self._confirm(self.enrollment).status_code, status.HTTP_403_FORBIDDEN)


class MakeStudentsForOldDataTests(APITestCase):
    def setUp(self):
        course = Course.objects.create(name='Python', full_amount=30000)
        self.lead = Lead.objects.create(full_name='Old Lead', phone='9800000950')
        self.active = Enrollment.objects.create(
            lead=self.lead, course=course, enrollment_date=date.today(),
            listed_fee=30000, agreed_amount=30000, status='active',
        )
        self.pending = Enrollment.objects.create(
            lead=self.lead, course=course, enrollment_date=date.today(),
            listed_fee=30000, agreed_amount=30000,
        )
        Lead.objects.create(full_name='Only pending', phone='9800000951')

    def _run(self, *args):
        out = StringIO()
        call_command('make_students_for_enrolled_leads', *args, stdout=out)
        return out.getvalue()

    def test_dry_run_changes_nothing(self):
        self.assertIn('Would make 1 students and link 1 enrollments', self._run('--dry-run'))
        self.assertFalse(Student.objects.exists())

    def test_makes_students_without_invoices(self):
        self.assertIn('Made 1 students and linked 1 enrollments', self._run())
        student = Student.objects.get()
        self.active.refresh_from_db()
        self.pending.refresh_from_db()
        self.assertEqual(self.active.student, student)
        self.assertIsNone(self.pending.student)
        self.lead.refresh_from_db()
        self.assertTrue(self.lead.is_converted)
        self.assertFalse(Invoice.objects.exists())
        # Running it again changes nothing.
        self.assertIn('Made 0 students and linked 0 enrollments', self._run())


class SeatTestBase(APITestCase):
    """Two Python batches with 2 seats each, and a Java batch."""

    def setUp(self):
        self.admin = User.objects.create_user(
            email='seats@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.course = Course.objects.create(name='Python', full_amount=30000)
        self.other_course = Course.objects.create(name='Java', full_amount=20000)
        self.batch = CourseBatch.objects.create(
            name='Python Nov', course=self.course, capacity=2, status='running',
        )
        self.next_batch = CourseBatch.objects.create(
            name='Python Dec', course=self.course, capacity=2,
        )
        self.java_batch = CourseBatch.objects.create(name='Java Nov', course=self.other_course)
        self.leads = 0

    def _lead(self):
        self.leads += 1
        return Lead.objects.create(full_name=f'Student {self.leads}', phone=f'98000010{self.leads:02d}')

    def _url(self, enrollment, action=''):
        return f'/api/leads/{enrollment.lead_id}/enrollments/{enrollment.id}/{action}'

    def _enroll(self, batch=None, course=None, lead=None, expect=status.HTTP_201_CREATED):
        lead = lead or self._lead()
        body = {
            'course': (course or self.course).id, 'enrollment_date': str(date.today()),
            'listed_fee': '30000', 'agreed_amount': '30000',
        }
        if batch is not None:
            body['batch'] = batch.id
        response = self.client.post(f'/api/leads/{lead.id}/enrollments/', body, format='json')
        self.assertEqual(response.status_code, expect, response.data)
        if expect != status.HTTP_201_CREATED:
            return response
        return Enrollment.objects.get(id=response.data['id'])

    def _confirm(self, enrollment):
        return self.client.post(self._url(enrollment, 'confirm/'))

    def _seats(self, batch):
        return self.client.get(f'/api/course-batches/{batch.id}/').data


class SeatTests(SeatTestBase):
    """Confirm takes a seat in the batch, cancel frees it, and full or closed batches refuse."""

    def test_confirm_takes_a_seat_in_the_chosen_batch(self):
        enrollment = self._enroll(self.batch)
        response = self._confirm(enrollment)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['current_batch'], self.batch.id)
        self.assertEqual(response.data['current_batch_name'], 'Python Nov')
        placement = Placement.objects.get(enrollment=enrollment)
        self.assertEqual((placement.kind, placement.course_batch), ('join', self.batch))
        self.assertEqual(placement.created_by, self.admin)
        self.assertEqual(enrollment.history.get().after['batch'], self.batch.coursebatch_id)
        seats = self._seats(self.batch)
        self.assertEqual((seats['seats_taken'], seats['seats_left']), (1, 1))

    def test_confirm_without_a_batch_takes_no_seat(self):
        enrollment = self._enroll()
        self.assertEqual(self._confirm(enrollment).status_code, status.HTTP_200_OK)
        enrollment.refresh_from_db()
        self.assertIsNone(enrollment.current_batch)
        self.assertFalse(Placement.objects.exists())

    def test_confirm_into_a_full_batch_is_refused_and_nothing_changes(self):
        for _ in range(2):
            self.assertEqual(self._confirm(self._enroll(self.batch)).status_code, 200)
        late = self._enroll(self.batch)

        response = self._confirm(late)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Python Nov is full (2 of 2 seats)', response.data['detail'])
        late.refresh_from_db()
        self.assertEqual(late.status, 'pending')
        self.assertIsNone(late.student)
        self.assertFalse(late.placements.exists())
        self.assertFalse(late.history.exists())
        self.assertEqual(Student.objects.count(), 2)
        self.assertEqual(Invoice.objects.count(), 2)

    def test_a_batch_with_no_seat_limit_never_fills(self):
        open_batch = CourseBatch.objects.create(name='Python Open', course=self.course)
        for _ in range(3):
            self.assertEqual(self._confirm(self._enroll(open_batch)).status_code, 200)
        seats = self._seats(open_batch)
        self.assertEqual((seats['seats_taken'], seats['seats_left']), (3, None))

    def test_confirm_into_a_closed_batch_is_refused(self):
        enrollment = self._enroll(self.batch)
        CourseBatch.objects.filter(pk=self.batch.pk).update(status='closed')
        response = self._confirm(enrollment)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Python Nov is closed', response.data['detail'])

    def test_cancel_frees_the_seat_and_reinstate_does_not_take_it_back(self):
        enrollment = self._enroll(self.batch)
        self._confirm(enrollment)
        response = self.client.post(
            self._url(enrollment, 'cancel/'), {'reason': 'Moved abroad'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        enrollment.refresh_from_db()
        self.assertIsNone(enrollment.current_batch)
        self.assertEqual(enrollment.batch, self.batch)  # the money batch stays
        leave = enrollment.placements.first()
        self.assertEqual((leave.kind, leave.course_batch, leave.from_batch, leave.reason),
                         ('leave', None, self.batch, 'Moved abroad'))
        self.assertEqual(self._seats(self.batch)['seats_taken'], 0)

        self.client.post(self._url(enrollment, 'reinstate/'), {'reason': 'Came back'}, format='json')
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.status, 'confirmed')
        self.assertIsNone(enrollment.current_batch)

    def test_cancel_of_a_pending_enrollment_adds_no_seat_row(self):
        enrollment = self._enroll(self.batch)
        self.client.post(self._url(enrollment, 'cancel/'), {'reason': 'Not joining'}, format='json')
        self.assertFalse(Placement.objects.exists())

    def test_batch_and_course_are_fixed_after_confirm(self):
        enrollment = self._enroll(self.batch)
        self._confirm(enrollment)
        response = self.client.patch(self._url(enrollment), {'batch': self.next_batch.id}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Use Move', str(response.data['batch']))
        response = self.client.patch(
            self._url(enrollment), {'course': self.other_course.id}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('course', response.data)

    def test_a_pending_enrollment_can_change_its_batch(self):
        enrollment = self._enroll(self.batch)
        response = self.client.patch(self._url(enrollment), {'batch': self.next_batch.id}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['batch'], self.next_batch.id)

    def test_seat_cannot_be_set_by_a_plain_edit(self):
        enrollment = self._enroll(self.batch)
        self.client.patch(self._url(enrollment), {'current_batch': self.next_batch.id}, format='json')
        enrollment.refresh_from_db()
        self.assertIsNone(enrollment.current_batch)

    def test_batch_of_another_course_is_refused(self):
        response = self._enroll(self.java_batch, expect=status.HTTP_400_BAD_REQUEST)
        self.assertIn('another course', str(response.data['batch']))

    def test_closed_batch_cannot_be_chosen(self):
        CourseBatch.objects.filter(pk=self.batch.pk).update(status='closed')
        response = self._enroll(self.batch, expect=status.HTTP_400_BAD_REQUEST)
        self.assertIn('closed', str(response.data['batch']))

    def test_dropped_out_students_do_not_hold_a_seat(self):
        enrollment = self._enroll(self.batch)
        self._confirm(enrollment)
        self.client.patch(self._url(enrollment), {'status': 'dropped_out'}, format='json')
        self.assertEqual(self._seats(self.batch)['seats_taken'], 0)

    def test_batch_shows_who_is_waiting(self):
        self._enroll(self.batch)
        self._enroll(self.batch)
        self._confirm(self._enroll(self.batch))
        seats = self._seats(self.batch)
        self.assertEqual((seats['seats_taken'], seats['waiting_count']), (1, 2))
        row = next(
            b for b in self.client.get('/api/course-batches/').data['results']
            if b['id'] == self.batch.id
        )
        self.assertEqual((row['seats_taken'], row['seats_left'], row['waiting_count']), (1, 1, 2))

    def test_seats_cannot_go_below_the_students_sitting_there(self):
        self._confirm(self._enroll(self.batch))
        self._confirm(self._enroll(self.batch))
        response = self.client.patch(
            f'/api/course-batches/{self.batch.id}/', {'capacity': 1}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('at least 2 seats', str(response.data['capacity']))
        response = self.client.patch(
            f'/api/course-batches/{self.batch.id}/', {'capacity': 5}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['seats_left'], 3)

    def test_a_batch_with_enrollments_cannot_change_course(self):
        self._enroll(self.batch)
        response = self.client.patch(
            f'/api/course-batches/{self.batch.id}/', {'course': self.other_course.id}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('course', response.data)

    def test_seat_history_rows_cannot_be_changed_or_deleted(self):
        enrollment = self._enroll(self.batch)
        self._confirm(enrollment)
        placement = Placement.objects.get()
        placement.reason = 'Changed'
        with self.assertRaises(ValidationError):
            placement.save()
        with self.assertRaises(ValidationError):
            placement.delete()

    def test_a_seat_row_needs_a_reason_and_the_right_batch(self):
        enrollment = self._enroll(self.batch)
        with self.assertRaises(ValidationError):
            Placement.objects.create(
                enrollment=enrollment, course_batch=self.batch, kind='join',
                started_on=date.today(), reason='  ',
            )
        with self.assertRaises(ValidationError):
            Placement.objects.create(
                enrollment=enrollment, course_batch=None, kind='join',
                started_on=date.today(), reason='No batch',
            )


class BackfillSeatsTests(APITestCase):
    """Old confirmed enrollments with a batch get a seat when seat tracking starts."""

    def test_only_confirmed_enrollments_with_a_batch_get_a_seat_once(self):
        course = Course.objects.create(name='Python', full_amount=30000)
        batch = CourseBatch.objects.create(name='Python Nov', course=course)
        lead = Lead.objects.create(full_name='Old Student', phone='9800002000')

        def enrollment(status_, with_batch=True):
            return Enrollment.objects.create(
                lead=lead, course=course, batch=batch if with_batch else None,
                enrollment_date=date(2026, 5, 1), status=status_,
                listed_fee=30000, agreed_amount=30000,
            )

        active = enrollment('active')
        pending = enrollment('pending')
        cancelled = enrollment('cancelled')
        no_batch = enrollment('confirmed', with_batch=False)

        self.assertEqual(backfill_seats(Enrollment, Placement), 1)
        self.assertEqual(backfill_seats(Enrollment, Placement), 0)

        active.refresh_from_db()
        self.assertEqual(active.current_batch, batch)
        seat = active.placements.get()
        self.assertEqual((seat.kind, seat.started_on, seat.created_by), ('join', date(2026, 5, 1), None))
        for other in (pending, cancelled, no_batch):
            other.refresh_from_db()
            self.assertIsNone(other.current_batch)
            self.assertFalse(other.placements.exists())


class MovePauseResumeTests(SeatTestBase):
    """Move, pause, resume and join change only the seat. The money batch stays."""

    def setUp(self):
        super().setUp()
        self.enrollment = self._enroll(self.batch)
        self._confirm(self.enrollment)

    def _act(self, action, enrollment=None, **body):
        enrollment = enrollment or self.enrollment
        return self.client.post(self._url(enrollment, f'{action}/'), body, format='json')

    def _reload(self, enrollment=None):
        enrollment = enrollment or self.enrollment
        enrollment.refresh_from_db()
        return enrollment

    def test_move_changes_the_seat_and_keeps_the_money_batch(self):
        response = self._act('move', batch=self.next_batch.id, reason='Evening suits her job')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['current_batch'], self.next_batch.id)
        self.assertEqual(response.data['batch'], self.batch.id)
        self.assertEqual(response.data['seat_state'], 'seated')
        newest = response.data['placements'][0]
        self.assertEqual(
            (newest['kind'], newest['course_batch_name'], newest['from_batch_name'], newest['reason']),
            ('move', 'Python Dec', 'Python Nov', 'Evening suits her job'),
        )
        row = self.enrollment.history.get(action='switch_batch')
        self.assertEqual(
            (row.before, row.after),
            ({'batch': self.batch.coursebatch_id}, {'batch': self.next_batch.coursebatch_id}),
        )
        self.assertEqual(self._seats(self.batch)['seats_taken'], 0)
        self.assertEqual(self._seats(self.next_batch)['seats_taken'], 1)

    def test_move_needs_a_reason(self):
        response = self._act('move', batch=self.next_batch.id)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('reason', response.data)
        response = self._act('move', batch=self.next_batch.id, reason='   ')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._reload().current_batch, self.batch)

    def test_move_to_the_same_batch_is_refused(self):
        response = self._act('move', batch=self.batch.id, reason='Same')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('already sits in Python Nov', response.data['detail'])

    def test_move_to_another_course_is_refused(self):
        response = self._act('move', batch=self.java_batch.id, reason='Wants Java')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('cancel this enrollment', response.data['detail'])

    def test_move_into_a_full_batch_is_refused_and_nothing_changes(self):
        for _ in range(2):
            self._confirm(self._enroll(self.next_batch))
        response = self._act('move', batch=self.next_batch.id, reason='Evening')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Python Dec is full', response.data['detail'])
        self.assertEqual(self._reload().current_batch, self.batch)
        self.assertEqual(self.enrollment.placements.count(), 1)
        self.assertFalse(self.enrollment.history.filter(action='switch_batch').exists())

    def test_no_one_moves_into_a_closed_batch_but_can_move_out(self):
        CourseBatch.objects.filter(pk=self.next_batch.pk).update(status='closed')
        response = self._act('move', batch=self.next_batch.id, reason='Evening')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('closed', response.data['detail'])

        CourseBatch.objects.filter(pk=self.next_batch.pk).update(status='running')
        CourseBatch.objects.filter(pk=self.batch.pk).update(status='closed')
        response = self._act('move', batch=self.next_batch.id, reason='Needs more practice')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def test_move_on_a_given_day(self):
        response = self._act('move', batch=self.next_batch.id, reason='Evening', started_on='2026-12-01')
        self.assertEqual(response.data['placements'][0]['started_on'], '2026-12-01')

    def test_pause_frees_the_seat(self):
        response = self._act('pause', reason='Exams')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertIsNone(response.data['current_batch'])
        self.assertEqual(response.data['seat_state'], 'paused')
        self.assertEqual(response.data['batch'], self.batch.id)
        pause = self.enrollment.placements.first()
        self.assertEqual((pause.kind, pause.course_batch, pause.from_batch), ('pause', None, self.batch))
        self.assertEqual(self.enrollment.history.get(action='pause').before, {'batch': self.batch.coursebatch_id})
        self.assertEqual(self._seats(self.batch)['seats_taken'], 0)

    def test_pause_needs_a_reason_and_a_seat(self):
        self.assertEqual(self._act('pause').status_code, status.HTTP_400_BAD_REQUEST)
        self._act('pause', reason='Exams')
        again = self._act('pause', reason='Again')
        self.assertEqual(again.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('nothing to pause', again.data['detail'])

    def test_resume_into_the_same_batch(self):
        self._act('pause', reason='Exams')
        response = self._act('resume', batch=self.batch.id, reason='Exams over')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['seat_state'], 'seated')
        self.assertEqual(response.data['current_batch'], self.batch.id)
        resume = self.enrollment.placements.first()
        self.assertEqual((resume.kind, resume.from_batch), ('resume', self.batch))
        row = self.enrollment.history.get(action='resume')
        self.assertEqual((row.before, row.after), ({'batch': None}, {'batch': self.batch.coursebatch_id}))

    def test_resume_into_a_later_batch(self):
        self._act('pause', reason='Exams')
        response = self._act('resume', batch=self.next_batch.id, reason='Joins the December batch')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(self._reload().current_batch, self.next_batch)
        self.assertEqual(self.enrollment.batch, self.batch)

    def test_resume_into_a_full_batch_is_refused_and_stays_paused(self):
        self._act('pause', reason='Exams')
        for _ in range(2):
            self._confirm(self._enroll(self.batch))
        response = self._act('resume', batch=self.batch.id, reason='Back')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('full', response.data['detail'])
        detail = self.client.get(self._url(self.enrollment)).data
        self.assertEqual(detail['seat_state'], 'paused')

    def test_only_a_paused_student_can_resume(self):
        response = self._act('resume', batch=self.next_batch.id, reason='Back')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Only a paused student', response.data['detail'])

    def test_join_seats_a_student_confirmed_without_a_batch(self):
        enrollment = self._enroll()
        self._confirm(enrollment)
        response = self._act('join-batch', enrollment, batch=self.next_batch.id)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['current_batch'], self.next_batch.id)
        self.assertEqual(response.data['batch'], self.next_batch.id)  # first batch = money batch
        row = enrollment.history.get(action='join_batch')
        self.assertEqual(row.reason, 'Joined the batch.')

    def test_join_is_refused_when_seated_paused_or_pending(self):
        response = self._act('join-batch', batch=self.next_batch.id)
        self.assertIn('Use Move', response.data['detail'])
        self._act('pause', reason='Exams')
        response = self._act('join-batch', batch=self.next_batch.id)
        self.assertIn('Use Resume', response.data['detail'])
        pending = self._enroll()
        response = self._act('join-batch', pending, batch=self.next_batch.id)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Confirm the enrollment first', response.data['detail'])

    def test_after_reinstate_join_takes_a_seat_and_the_money_batch_stays(self):
        self._act('cancel', reason='Moved abroad')
        self._act('reinstate', reason='Came back')
        response = self._act('join-batch', batch=self.next_batch.id, reason='Back for December')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['current_batch'], self.next_batch.id)
        self.assertEqual(response.data['batch'], self.batch.id)

    def test_a_cancelled_enrollment_has_no_seat_actions(self):
        self._act('cancel', reason='Moved abroad')
        response = self._act('pause', reason='x')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('cancelled', response.data['detail'])

    def test_seat_history_is_listed_newest_first(self):
        self._act('pause', reason='Exams')
        self._act('resume', batch=self.next_batch.id, reason='Back')
        detail = self.client.get(self._url(self.enrollment)).data
        self.assertEqual([p['kind'] for p in detail['placements']], ['resume', 'pause', 'join'])

    def test_batch_students_show_who_moved_in_and_who_is_paused(self):
        mover = self.enrollment
        stayer = self._enroll(self.batch)
        self._confirm(stayer)
        paused = self._enroll(self.next_batch)
        self._confirm(paused)
        self._act('move', mover, batch=self.next_batch.id, reason='Evening')
        self._act('pause', paused, reason='Exams')

        roster = self.client.get(f'/api/course-batches/{self.next_batch.id}/students/').data
        self.assertEqual([r['enrollment'] for r in roster['seated']], [mover.id])
        row = roster['seated'][0]
        self.assertEqual(
            (row['how'], row['from_batch_name'], row['money_batch_name'], row['moved_in']),
            ('move', 'Python Nov', 'Python Nov', True),
        )
        self.assertEqual([r['enrollment'] for r in roster['paused']], [paused.id])
        self.assertFalse(roster['paused'][0]['moved_in'])

        first = self.client.get(f'/api/course-batches/{self.batch.id}/students/').data
        self.assertEqual([r['enrollment'] for r in first['seated']], [stayer.id])
        self.assertEqual((first['seated'][0]['how'], first['seated'][0]['moved_in']), ('join', False))
        self.assertEqual(first['paused'], [])


class DueDateTests(SeatTestBase):
    """Due dates: set at confirm, changed with a reason, filled oldest first."""

    def _day(self, days):
        return str(date.today() + timedelta(days=days))

    def _confirmed(self, due_dates=None):
        enrollment = self._enroll()
        body = {'due_dates': due_dates} if due_dates is not None else {}
        response = self.client.post(self._url(enrollment, 'confirm/'), body, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return enrollment

    def _rows(self, enrollment):
        return self.client.get(self._url(enrollment)).data['due_dates']

    def _change(self, enrollment, due_dates, reason='Asked to pay later'):
        return self.client.post(
            self._url(enrollment, 'due-dates/'),
            {'due_dates': due_dates, 'reason': reason}, format='json',
        )

    def _pay(self, enrollment, amount, pay_status='successful'):
        return Payment.objects.create(
            enrollment=enrollment, amount=amount, payment_date=timezone.now(),
            payment_method='cash', status=pay_status,
        )

    def _three_parts(self, first=0):
        return [
            {'due_on': self._day(first + 60), 'amount': '10000'},
            {'due_on': self._day(first), 'amount': '10000'},
            {'due_on': self._day(first + 30), 'amount': '10000'},
        ]

    def test_without_due_dates_the_whole_amount_is_due_on_the_confirm_day(self):
        enrollment = self._confirmed()
        rows = self._rows(enrollment)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['due_on'], str(date.today()))
        self.assertEqual(rows[0]['amount'], '30000.00')
        self.assertEqual(rows[0]['status'], 'due')
        plan = PaymentPlan.objects.get(enrollment=enrollment)
        self.assertEqual(plan.reason, 'Set when the enrollment was confirmed.')
        confirm = enrollment.history.get(action='confirm')
        self.assertEqual(
            confirm.after['due_dates'], [{'due_on': str(date.today()), 'amount': '30000.00'}],
        )

    def test_confirm_with_parts_lists_them_oldest_first(self):
        enrollment = self._confirmed(self._three_parts())
        rows = self._rows(enrollment)
        self.assertEqual(
            [r['due_on'] for r in rows], [self._day(0), self._day(30), self._day(60)],
        )
        self.assertEqual({r['status'] for r in rows}, {'due'})

    def test_parts_must_add_up_to_the_agreed_amount(self):
        enrollment = self._enroll()
        response = self.client.post(
            self._url(enrollment, 'confirm/'),
            {'due_dates': [{'due_on': self._day(0), 'amount': '20000'}]}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('add up to 20000', response.data['detail'])
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.status, 'pending')
        self.assertFalse(PaymentPlan.objects.exists())
        self.assertFalse(Student.objects.exists())

    def test_payments_fill_the_oldest_part_first(self):
        enrollment = self._confirmed(self._three_parts())
        self._pay(enrollment, 15000)
        self._pay(enrollment, 5000, pay_status='pending')  # not money yet
        rows = self._rows(enrollment)
        self.assertEqual([r['status'] for r in rows], ['paid', 'part_paid', 'due'])
        self.assertEqual([r['left'] for r in rows], ['0.00', '5000.00', '10000.00'])

    def test_an_unpaid_part_past_its_due_date_is_late(self):
        enrollment = self._confirmed()
        response = self._change(enrollment, self._three_parts(first=-40))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self._pay(enrollment, 10000)
        rows = self._rows(enrollment)
        self.assertEqual([r['status'] for r in rows], ['paid', 'late', 'due'])
        self.assertEqual(rows[1]['days_late'], 10)
        self.assertEqual(rows[0]['days_late'], 0)

    def test_changing_due_dates_keeps_the_old_plan_and_writes_history(self):
        enrollment = self._confirmed()
        response = self._change(enrollment, self._three_parts())
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(len(response.data['due_dates']), 3)
        self.assertEqual(PaymentPlan.objects.filter(enrollment=enrollment).count(), 2)
        row = enrollment.history.get(action='change_due_dates')
        self.assertEqual(row.reason, 'Asked to pay later')
        self.assertEqual(row.before['due_dates'], [{'due_on': self._day(0), 'amount': '30000.00'}])
        self.assertEqual(len(row.after['due_dates']), 3)

    def test_changing_due_dates_needs_a_reason_and_the_right_total(self):
        enrollment = self._confirmed()
        self.assertEqual(
            self._change(enrollment, self._three_parts(), reason='  ').status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        response = self._change(enrollment, [{'due_on': self._day(5), 'amount': '29000'}])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['detail'], (
            'The due dates add up to 29000.00, but the agreed amount is 30000.00.'
        ))
        self.assertEqual(PaymentPlan.objects.filter(enrollment=enrollment).count(), 1)

    def test_a_pending_enrollment_has_no_due_dates(self):
        enrollment = self._enroll()
        self.assertEqual(self._rows(enrollment), [])
        response = self._change(enrollment, self._three_parts())
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('confirmed enrollment', response.data['detail'])

    def test_discounts_and_write_offs_fill_parts_and_refunds_take_back(self):
        enrollment = self._confirmed(self._three_parts())
        url = self._url(enrollment, 'adjustments/')
        self._pay(enrollment, 10000)
        self.client.post(url, {'kind': 'discount', 'amount': '4000', 'reason': 'Promo'}, format='json')
        self.client.post(url, {'kind': 'write_off', 'amount': '1000', 'reason': 'Approved'}, format='json')
        rows = self._rows(enrollment)
        self.assertEqual([r['left'] for r in rows], ['0.00', '5000.00', '10000.00'])
        self.client.post(url, {'kind': 'refund', 'amount': '5000', 'reason': 'Overpaid'}, format='json')
        rows = self._rows(enrollment)
        self.assertEqual([r['left'] for r in rows], ['0.00', '10000.00', '10000.00'])

    def test_a_voided_payment_no_longer_fills_a_part(self):
        enrollment = self._confirmed(self._three_parts())
        payment = self._pay(enrollment, 10000)
        self.assertEqual(self._rows(enrollment)[0]['status'], 'paid')
        self.client.post(
            f'/api/leads/{enrollment.lead_id}/enrollments/{enrollment.id}/payments/{payment.id}/void/',
            {'reason': 'Wrong amount'}, format='json',
        )
        self.assertEqual(self._rows(enrollment)[0]['status'], 'due')

    def test_an_extra_charge_is_due_on_the_day_it_is_added(self):
        enrollment = self._confirmed([{'due_on': self._day(30), 'amount': '30000'}])
        self.client.post(
            self._url(enrollment, 'adjustments/'),
            {'kind': 'extra_charge', 'amount': '2000', 'reason': 'Exam fee'}, format='json',
        )
        rows = self._rows(enrollment)
        self.assertEqual([r['kind'] for r in rows], ['extra_charge', 'part'])
        self.assertEqual(rows[0]['due_on'], str(date.today()))
        self.assertEqual(rows[0]['amount'], '2000.00')

    def test_an_old_enrollment_without_a_plan_has_one_part_with_no_due_date(self):
        enrollment = Enrollment.objects.create(
            lead=self._lead(), course=self.course, enrollment_date=date(2025, 1, 1),
            listed_fee=30000, agreed_amount=30000, status='active',
        )
        rows = self._rows(enrollment)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]['due_on'])
        self.assertEqual(rows[0]['status'], 'due')

    def test_plans_and_due_dates_cannot_be_changed_or_deleted(self):
        enrollment = self._confirmed(self._three_parts())
        plan = PaymentPlan.objects.get(enrollment=enrollment)
        part = plan.installments.first()
        part.amount = 1
        with self.assertRaises(ValidationError):
            part.save()
        with self.assertRaises(ValidationError):
            part.delete()
        with self.assertRaises(ValidationError):
            plan.save()
        with self.assertRaises(ValidationError):
            plan.delete()


class ReissueFeeInvoiceTests(SeatTestBase):
    """A new fee invoice can be made once the old one is cancelled."""

    def test_a_new_fee_invoice_after_the_old_one_is_cancelled(self):
        enrollment = self._enroll()
        self._confirm(enrollment)
        url = self._url(enrollment, 'fee-invoice/')
        self.assertTrue(self.client.get(self._url(enrollment)).data['has_fee_invoice'])

        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['detail'], 'This enrollment already has an active fee invoice.')

        old = Invoice.objects.get(enrollment=enrollment, type='enrollment')
        self.client.post(f'/api/invoices/{old.id}/cancel/', {'reason': 'Wrong name'}, format='json')
        self.assertFalse(self.client.get(self._url(enrollment)).data['has_fee_invoice'])

        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertNotEqual(response.data['invoice_number'], old.invoice_number)
        self.assertEqual(response.data['amount'], '30000.00')
        self.assertTrue(self.client.get(self._url(enrollment)).data['has_fee_invoice'])

    def test_a_pending_enrollment_gets_no_fee_invoice(self):
        enrollment = self._enroll()
        response = self.client.post(self._url(enrollment, 'fee-invoice/'))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Invoice.objects.exists())


class ReminderTestBase(APITestCase):
    """Reminders on, a counselor, and a helper to make enrollments with due dates."""

    TODAY = date(2026, 12, 1)

    def setUp(self):
        from unittest import mock

        from django.core import mail

        self.mock = mock
        self.mail = mail
        self.counselor = User.objects.create_user(
            email='counselor@test.com', name='Ram', password='pass',
        )
        self.course = Course.objects.create(name='Python', full_amount=30000)
        self.config = ReminderConfig.load()
        self.config.is_active = True
        self.config.save()
        self.people = 0

    def _enrollment(self, parts, *, email='student@test.com', status='confirmed', paid=0):
        """parts: list of (days from TODAY, amount)."""
        self.people += 1
        lead = Lead.objects.create(
            full_name=f'Student {self.people}', phone=f'98000030{self.people:02d}',
            email=email, assigned_counselor=self.counselor,
        )
        enrollment = Enrollment.objects.create(
            lead=lead, course=self.course, enrollment_date=self.TODAY,
            listed_fee=30000, agreed_amount=sum(a for _, a in parts), status=status,
        )
        plan = PaymentPlan.objects.create(enrollment=enrollment, reason='Plan', created_by=self.counselor)
        for days, amount in parts:
            Installment.objects.create(
                plan=plan, due_on=self.TODAY + timedelta(days=days), amount=amount,
            )
        if paid:
            Payment.objects.create(
                enrollment=enrollment, amount=paid, payment_date=timezone.now(),
                payment_method='cash', status='successful',
            )
        return enrollment

    def _run(self, days=0, **kwargs):
        from apps.lead.reminders import send_payment_reminders

        return send_payment_reminders(
            today=self.TODAY + timedelta(days=days), config=self.config, **kwargs,
        )



class PaymentReminderTests(ReminderTestBase):
    """The daily job: each step once per due date, paused and paid get none."""

    def test_step_for_each_window(self):
        from apps.lead.reminders import step_for

        self.assertIsNone(step_for(-4, self.config))
        self.assertEqual(step_for(-3, self.config), 'before_due')
        self.assertEqual(step_for(0, self.config), 'due_today')
        self.assertEqual(step_for(6, self.config), 'due_today')
        self.assertEqual(step_for(7, self.config), 'late')
        self.assertEqual(step_for(15, self.config), 'call')
        self.assertEqual(step_for(90, self.config), 'call')

    def test_each_step_goes_out_once(self):
        enrollment = self._enrollment([(3, 30000)])
        self._run(0)
        self._run(0)  # the same day again sends nothing
        self.assertEqual(len(self.mail.outbox), 1)
        self.assertEqual(self.mail.outbox[0].subject, 'Payment reminder - Skill Shikshya')
        self.assertEqual(self.mail.outbox[0].to, ['student@test.com'])
        self.assertIn('NPR 30,000 due on 4 Dec 2026', self.mail.outbox[0].body)
        for day in (1, 2, 3, 4, 10, 11, 18, 19, 30):
            self._run(day)
        steps = list(
            ReminderLog.objects.filter(enrollment=enrollment).order_by('id').values_list('step', flat=True)
        )
        self.assertEqual(steps, ['before_due', 'due_today', 'late', 'call'])
        self.assertEqual(len(self.mail.outbox), 3)
        self.assertEqual(self.mail.outbox[1].subject, 'Payment due - Skill Shikshya')
        self.assertEqual(self.mail.outbox[2].subject, 'Payment overdue - Skill Shikshya')

    def test_the_call_step_makes_a_follow_up_for_the_counselor(self):
        enrollment = self._enrollment([(-20, 30000)])
        self._run()
        followup = FollowUp.objects.get()
        self.assertEqual(followup.enrollment, enrollment)
        self.assertEqual(followup.assigned_to, self.counselor)
        self.assertEqual(followup.status, 'pending')
        self.assertIn('Payment late (Python): NPR 30,000 due on 11 Nov 2026 (20 days late)', followup.purpose)
        log = ReminderLog.objects.get()
        self.assertEqual((log.step, log.channel, log.status), ('call', 'follow_up', 'sent'))
        self.assertEqual(log.followup, followup)
        self.assertEqual(len(self.mail.outbox), 0)

    def test_a_missed_day_sends_only_the_step_reached(self):
        self._enrollment([(-10, 30000)])
        self._run()
        self.assertEqual(list(ReminderLog.objects.values_list('step', flat=True)), ['late'])

    def test_paid_and_part_paid_due_dates(self):
        self._enrollment([(0, 10000), (0, 10000)], paid=20000)
        part_paid = self._enrollment([(0, 10000)], paid=4000)
        report = self._run()
        self.assertEqual([r['enrollment'] for r in report], [part_paid.id])
        self.assertEqual(report[0]['amount'], Decimal('6000.00'))

    def test_due_dates_on_the_same_run_go_in_one_email(self):
        enrollment = self._enrollment([(-8, 10000), (0, 10000), (40, 10000)])
        self._run()
        self.assertEqual(len(self.mail.outbox), 1)
        self.assertEqual(self.mail.outbox[0].subject, 'Payment overdue - Skill Shikshya')
        self.assertEqual(
            sorted(ReminderLog.objects.filter(enrollment=enrollment).values_list('step', flat=True)),
            ['due_today', 'late'],
        )

    def test_paused_pending_and_cancelled_get_nothing(self):
        paused = self._enrollment([(0, 30000)])
        Placement.objects.create(
            enrollment=paused, kind='pause', from_batch=CourseBatch.objects.create(
                name='Python Nov', course=self.course,
            ), started_on=self.TODAY, reason='Exams', created_by=self.counselor,
        )
        self._enrollment([(0, 30000)], status='pending')
        self._enrollment([(0, 30000)], status='cancelled')
        self.assertEqual(self._run(), [])

    def test_no_email_address_is_logged_as_skipped_once(self):
        self._enrollment([(0, 30000)], email=None)
        self._run()
        self._run()
        log = ReminderLog.objects.get()
        self.assertEqual((log.status, log.error), ('skipped', 'No email address.'))
        self.assertEqual(len(self.mail.outbox), 0)

    def test_a_failed_email_is_tried_again_next_run(self):
        self._enrollment([(0, 30000)])
        with self.mock.patch('apps.lead.reminders.send_mail', side_effect=OSError('SMTP down')):
            self._run()
        log = ReminderLog.objects.get()
        self.assertEqual((log.status, log.error), ('failed', 'SMTP down'))
        self._run(1)
        self.assertEqual(
            list(ReminderLog.objects.order_by('id').values_list('status', flat=True)),
            ['failed', 'sent'],
        )
        self.assertEqual(len(self.mail.outbox), 1)

    def test_dry_run_sends_nothing(self):
        self._enrollment([(0, 30000)])
        report = self._run(dry_run=True)
        self.assertEqual(len(report), 1)
        self.assertFalse(ReminderLog.objects.exists())
        self.assertEqual(len(self.mail.outbox), 0)

    def test_the_task_does_nothing_while_reminders_are_off(self):
        from apps.lead.tasks import send_payment_reminders_task

        self._enrollment([(0, 30000)])
        self.config.is_active = False
        self.config.save()
        self.assertEqual(send_payment_reminders_task(), 0)
        self.assertFalse(ReminderLog.objects.exists())


class PaymentFollowUpApiTests(ReminderTestBase):
    """Reminder list on an enrollment, and the Payment filter on follow-ups."""

    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user(
            email='followups@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.client.force_authenticate(self.admin)

    def test_an_enrollment_lists_its_reminders(self):
        enrollment = self._enrollment([(-20, 10000), (0, 20000)])
        self._run()
        response = self.client.get(
            f'/api/leads/{enrollment.lead_id}/enrollments/{enrollment.id}/reminders/',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = response.data['results']
        self.assertEqual(
            sorted((r['step'], r['channel'], r['status']) for r in rows),
            [('call', 'follow_up', 'sent'), ('due_today', 'email', 'sent')],
        )
        self.assertEqual({r['step_display'] for r in rows}, {'Call follow-up', 'Due today'})

    def test_follow_ups_filter_by_payment(self):
        enrollment = self._enrollment([(-20, 30000)])
        self._run()
        FollowUp.objects.create(lead=enrollment.lead, scheduled_at=timezone.now(), purpose='Chat')
        payment = self.client.get('/api/followups/', {'payment': 'true'}).data['results']
        self.assertEqual(len(payment), 1)
        self.assertEqual(payment[0]['enrollment'], enrollment.id)
        self.assertEqual(payment[0]['enrollment_course_name'], 'Python')
        others = self.client.get('/api/followups/', {'payment': 'false'}).data['results']
        self.assertEqual([f['purpose'] for f in others], ['Chat'])

    def test_a_follow_up_cannot_be_linked_to_an_enrollment_by_hand(self):
        enrollment = self._enrollment([(5, 30000)])
        response = self.client.post(
            f'/api/leads/{enrollment.lead_id}/followups/',
            {'scheduled_at': timezone.now().isoformat(), 'enrollment': enrollment.id},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertIsNone(response.data['enrollment'])


class TeamsLicenceTests(APITestCase):
    """Teams licences are given to seated students and freed with a reason."""

    URL = '/api/teams-licences/'

    def setUp(self):
        from apps.lead.models import TeamsConfig

        self.admin = User.objects.create_user(
            email='teams@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.course = Course.objects.create(name='Python', full_amount=30000)
        self.batch = CourseBatch.objects.create(name='Python Nov', course=self.course, status='running')
        TeamsConfig.objects.create(total_licences=2)
        self.people = 0

    def _student(self, *, seated=True, status='confirmed'):
        self.people += 1
        lead = Lead.objects.create(full_name=f'Teams {self.people}', phone=f'98000040{self.people:02d}')
        return Enrollment.objects.create(
            lead=lead, course=self.course, enrollment_date=timezone.localdate(),
            listed_fee=30000, agreed_amount=30000, status=status,
            batch=self.batch, current_batch=self.batch if seated else None,
        )

    def _give(self, enrollment, account):
        return self.client.post(
            self.URL, {'enrollment': enrollment.id, 'account': account}, format='json',
        )

    def _free(self, licence_id, reason='Course over'):
        return self.client.post(f'{self.URL}{licence_id}/free/', {'reason': reason}, format='json')

    def test_give_and_free_a_licence(self):
        student = self._student()
        response = self._give(student, ' Teams1@SkillShikshya.com ')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['account'], 'teams1@skillshikshya.com')
        self.assertEqual(response.data['batch_name'], 'Python Nov')
        self.assertEqual(self.client.get(f'{self.URL}summary/').data, {'total': 2, 'in_use': 1, 'free': 1})

        lead_url = f'/api/leads/{student.lead_id}/enrollments/{student.id}/'
        self.assertEqual(self.client.get(lead_url).data['teams_licence']['account'], 'teams1@skillshikshya.com')

        licence_id = response.data['id']
        self.assertEqual(self._free(licence_id, reason=' ').status_code, 400)
        response = self._free(licence_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['release_reason'], 'Course over')
        self.assertEqual(response.data['released_by_name'], 'Admin')
        self.assertEqual(self._free(licence_id).data['detail'], 'This licence is already free.')
        self.assertIsNone(self.client.get(lead_url).data['teams_licence'])

        actions = list(student.history.order_by('created_at', 'id').values_list('action', flat=True))
        self.assertEqual(actions, ['give_licence', 'free_licence'])

        # The same account can be given again once it is free.
        self.assertEqual(self._give(student, 'teams1@skillshikshya.com').status_code, 201)

    def test_one_licence_per_student_and_per_account(self):
        a, b = self._student(), self._student()
        self.assertEqual(self._give(a, 't1@x.com').status_code, 201)
        self.assertEqual(self._give(a, 't2@x.com').data['detail'], 'This student already has a Teams licence.')
        response = self._give(b, 'T1@x.com')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['detail'], 't1@x.com is in use by Teams 1. Free it first.')

    def test_no_more_than_the_total(self):
        for n in range(2):
            self.assertEqual(self._give(self._student(), f't{n}@x.com').status_code, 201)
        response = self._give(self._student(), 't9@x.com')
        self.assertEqual(response.data['detail'], 'All 2 Teams licences are in use. Free one first.')

    def test_only_seated_students_get_one(self):
        message = 'Only a student sitting in a batch can get a Teams licence.'
        self.assertEqual(self._give(self._student(seated=False), 'a@x.com').data['detail'], message)
        self.assertEqual(
            self._give(self._student(seated=False, status='pending'), 'b@x.com').data['detail'], message,
        )

    def test_pause_cancel_and_batch_close_free_the_licence(self):
        from apps.course.services import close_batch

        paused, cancelled, closing = self._student(), self._student(), self._student()
        for n, e in enumerate((paused, cancelled)):
            self._give(e, f'p{n}@x.com')
        services.pause_enrollment(paused, reason='Exams', user=self.admin)
        services.cancel_enrollment(cancelled, reason='Moved abroad', user=self.admin)
        self._give(closing, 'c@x.com')
        close_batch(self.batch, reason='Finished', user=self.admin)

        freed = {
            t.enrollment_id: t.release_reason
            for t in TeamsLicence.objects.filter(released_at__isnull=False)
        }
        self.assertEqual(freed, {
            paused.id: 'Paused: Exams',
            cancelled.id: 'Cancelled: Moved abroad',
            closing.id: 'Batch closed: Finished',
        })
        self.assertEqual(self.client.get(f'{self.URL}summary/').data['in_use'], 0)

    def test_list_open_licences(self):
        a, b = self._student(), self._student()
        first = self._give(a, 'a@x.com').data['id']
        self._give(b, 'b@x.com')
        self._free(first)
        response = self.client.get(self.URL, {'open': 'true'})
        self.assertEqual([r['account'] for r in response.data['results']], ['b@x.com'])
        response = self.client.get(self.URL, {'open': 'false'})
        self.assertEqual([r['account'] for r in response.data['results']], ['a@x.com'])

    def test_licences_cannot_be_changed_or_deleted(self):
        licence = TeamsLicence.objects.create(
            enrollment=self._student(), account='z@x.com', assigned_by=self.admin,
        )
        licence.account = 'y@x.com'
        with self.assertRaises(ValidationError):
            licence.save()
        with self.assertRaises(ValidationError):
            licence.delete()

    def test_giving_needs_permission(self):
        clerk = User.objects.create_user(email='teams-clerk@test.com', name='Clerk', password='pass')
        self.client.force_authenticate(clerk)
        self.assertEqual(self._give(self._student(), 'q@x.com').status_code, 403)
        self.assertEqual(self.client.get(self.URL).status_code, 200)


class EnquiryTests(SeatTestBase):
    """One enquiry per course a person asks about, each with its own stage."""

    def setUp(self):
        super().setUp()
        self.source = LeadSource.objects.create(name='Facebook')
        self.counselor = User.objects.create_user(email='c@test.com', name='Counselor', password='pass')
        self.lead = Lead.objects.create(
            full_name='Ram Thapa', phone='9800000700', source=self.source,
            assigned_counselor=self.counselor,
        )

    def _enquiries_url(self, lead=None):
        return f'/api/leads/{(lead or self.lead).id}/course-interests/'

    def _add(self, course=None, expect=201, **extra):
        response = self.client.post(
            self._enquiries_url(), {'course': (course or self.course).id, **extra}, format='json',
        )
        self.assertEqual(response.status_code, expect, response.data)
        return response.data

    def _act(self, enquiry_id, action, body, expect=200):
        response = self.client.post(f'{self._enquiries_url()}{enquiry_id}/{action}/', body, format='json')
        self.assertEqual(response.status_code, expect, response.data)
        return response.data

    def test_new_enquiry_takes_source_and_counselor_from_the_lead(self):
        data = self._add()
        self.assertEqual(data['stage'], 'new')
        self.assertEqual(data['stage_display'], 'New')
        self.assertEqual(data['source_name'], 'Facebook')
        self.assertEqual(data['counselor_name'], 'Counselor')
        self.assertEqual(data['created_by'], self.admin.id)

    def test_one_open_enquiry_per_course(self):
        self._add()
        refused = self._add(expect=400)
        self.assertEqual(str(refused['detail']), 'Ram Thapa already has an open enquiry for Python.')
        self._add(course=self.other_course)
        self.assertEqual(self.lead.course_interests.count(), 2)

    def test_stage_moves_are_kept_in_history(self):
        enquiry = self._add()
        self._act(enquiry['id'], 'stage', {'stage': 'contacted'})
        data = self._act(enquiry['id'], 'stage', {'stage': 'interested', 'note': 'Wants evening'})
        self.assertEqual(data['stage'], 'interested')
        history = self.client.get(f"{self._enquiries_url()}{enquiry['id']}/history/").data
        self.assertEqual(
            [(h['from_stage'], h['to_stage'], h['note']) for h in history],
            [('contacted', 'interested', 'Wants evening'), ('new', 'contacted', '')],
        )
        self.assertEqual(history[0]['changed_by_name'], 'Admin')

    def test_won_and_same_stage_are_refused(self):
        enquiry = self._add()
        refused = self._act(enquiry['id'], 'stage', {'stage': 'won'}, expect=400)
        self.assertIn('Won is set by confirming', str(refused['detail']))
        refused = self._act(enquiry['id'], 'stage', {'stage': 'new'}, expect=400)
        self.assertEqual(str(refused['detail']), 'The enquiry is already new.')

    def test_lost_keeps_reason_and_stage(self):
        enquiry = self._add()
        self._act(enquiry['id'], 'stage', {'stage': 'contacted'})
        refused = self._act(enquiry['id'], 'lose', {'reason': 'other'}, expect=400)
        self.assertEqual(str(refused['detail']), 'Write the reason in the note.')
        data = self._act(enquiry['id'], 'lose', {'reason': 'too_expensive', 'note': 'Wants 20k'})
        self.assertEqual(
            (data['stage'], data['lost_reason'], data['lost_at_stage'], data['lost_note']),
            ('lost', 'too_expensive', 'contacted', 'Wants 20k'),
        )
        self.assertEqual(data['lost_reason_display'], 'Too expensive')
        last = EnquiryStageChange.objects.filter(enquiry_id=enquiry['id']).first()
        self.assertEqual(last.note, 'Too expensive: Wants 20k')
        self._act(enquiry['id'], 'lose', {'reason': 'timing'}, expect=400)

    def test_reopen_a_lost_enquiry_needs_a_note(self):
        enquiry = self._add()
        self._act(enquiry['id'], 'lose', {'reason': 'not_reachable'})
        refused = self._act(enquiry['id'], 'stage', {'stage': 'contacted'}, expect=400)
        self.assertEqual(str(refused['detail']), 'Say why the lost enquiry is opened again.')
        data = self._act(enquiry['id'], 'stage', {'stage': 'contacted', 'note': 'Called back'})
        self.assertEqual((data['stage'], data['lost_reason'], data['lost_at_stage']), ('contacted', None, None))
        # A lost enquiry does not block a new one for the same course.
        self._act(enquiry['id'], 'lose', {'reason': 'timing'})
        self._add()

    def test_the_stage_cannot_be_set_by_edit(self):
        enquiry = self._add()
        url = f"{self._enquiries_url()}{enquiry['id']}/"
        response = self.client.patch(url, {'stage': 'won', 'interest_level': 'high'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.data['stage'], response.data['interest_level']), ('new', 'high'))

    def test_course_is_fixed_once_past_new(self):
        enquiry = self._add()
        url = f"{self._enquiries_url()}{enquiry['id']}/"
        self.assertEqual(self.client.patch(url, {'course': self.other_course.id}, format='json').status_code, 200)
        self._act(enquiry['id'], 'stage', {'stage': 'contacted'})
        response = self.client.patch(url, {'course': self.course.id}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_only_an_untouched_enquiry_can_be_removed(self):
        first = self._add()
        url = f"{self._enquiries_url()}{first['id']}/"
        self.assertEqual(self.client.delete(url).status_code, 204)
        second = self._add()
        self._act(second['id'], 'stage', {'stage': 'contacted'})
        response = self.client.delete(f"{self._enquiries_url()}{second['id']}/")
        self.assertEqual(response.status_code, 400)
        self.assertIn('Mark it lost instead', str(response.data['detail']))

    def test_history_rows_cannot_change(self):
        enquiry = self._add()
        self._act(enquiry['id'], 'stage', {'stage': 'contacted'})
        row = EnquiryStageChange.objects.get()
        row.note = 'changed'
        with self.assertRaises(ValidationError):
            row.save()
        with self.assertRaises(ValidationError):
            row.delete()

    def test_confirming_an_enrollment_wins_the_enquiry(self):
        enquiry = self._add()
        enrollment = self._enroll(batch=self.batch, lead=self.lead)
        self.assertEqual(self._confirm(enrollment).status_code, 200)
        won = LeadCourseInterest.objects.get(id=enquiry['id'])
        self.assertEqual((won.stage, won.enrollment), ('won', enrollment))
        self.assertEqual(won.stage_changes.first().note, 'Enrollment confirmed.')
        refused = self._act(enquiry['id'], 'lose', {'reason': 'timing'}, expect=400)
        self.assertIn('Only an open enquiry can be lost', str(refused['detail']))

    def test_a_lost_enquiry_is_won_when_the_person_comes_back(self):
        enquiry = self._add()
        self._act(enquiry['id'], 'lose', {'reason': 'too_expensive'})
        enrollment = self._enroll(lead=self.lead)
        self._confirm(enrollment)
        won = LeadCourseInterest.objects.get(id=enquiry['id'])
        self.assertEqual((won.stage, won.lost_reason, won.enrollment), ('won', 'too_expensive', enrollment))

    def test_a_walk_in_gets_a_won_enquiry(self):
        enrollment = self._enroll(lead=self.lead)
        self._confirm(enrollment)
        won = self.lead.course_interests.get()
        self.assertEqual((won.stage, won.course, won.source, won.is_primary), ('won', self.course, self.source, True))

    def test_lead_with_a_main_course_gets_a_primary_enquiry(self):
        response = self.client.patch(
            f'/api/leads/{self.lead.id}/', {'primary_course': self.course.id}, format='json',
        )
        self.assertEqual(response.status_code, 200)
        enquiry = self.lead.course_interests.get()
        self.assertEqual((enquiry.course, enquiry.is_primary, enquiry.stage), (self.course, True, 'new'))
        # Changing the main course keeps the old enquiry and marks the new one primary.
        self.client.patch(f'/api/leads/{self.lead.id}/', {'primary_course': self.other_course.id}, format='json')
        self.assertEqual(
            list(self.lead.course_interests.order_by('id').values_list('course__name', 'is_primary')),
            [('Python', False), ('Java', True)],
        )

    def test_intake_adds_one_enquiry_per_course(self):
        response = self.client.post('/api/public/lead-intake/', {
            'name': 'Web Lead', 'phone_number': '9800000777', 'source_name': 'facebook',
            'course_interests': 'Python, Java, Python',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        lead = Lead.objects.get(id=response.data['id'])
        self.assertEqual(
            sorted(lead.course_interests.values_list('course__name', 'source__name', 'stage')),
            [('Java', 'Facebook', 'new'), ('Python', 'Facebook', 'new')],
        )

    def test_filter_by_stage(self):
        first = self._add()
        self._add(course=self.other_course)
        self._act(first['id'], 'stage', {'stage': 'contacted'})
        data = self.client.get(f'{self._enquiries_url()}?stage=contacted').data
        rows = data['results'] if isinstance(data, dict) else data
        self.assertEqual([r['id'] for r in rows], [first['id']])

