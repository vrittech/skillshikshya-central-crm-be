from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.db.models import ProtectedError
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.course.models import Course, CourseBatch
from apps.invoices.models import Invoice
from apps.lead.models import (
    Adjustment, Lead, Enrollment, Payment, EnrollmentHistory, Placement, PaymentPlan,
    Installment, FollowUp, ReminderConfig, ReminderLog, TeamsAccount, TeamsLicence, LeadCourseInterest,
    EnquiryStageChange, LeadSource, VoiceNote,
)
from apps.lead import services
from apps.lead.seat_backfill import backfill_seats
from apps.student.models import Student
from apps.global_config.testing import give_rights, give_view_rights

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

    def test_filter_whole_days(self):
        from django.utils import timezone as tz
        today = tz.localdate().isoformat()
        response = self.client.get('/api/calls/', {'date_from': today, 'date_to': today})
        self.assertEqual([r['id'] for r in response.data['results']], [self.recent_call.id])
        old_day = tz.localdate(self.old_call.call_timestamp).isoformat()
        response = self.client.get('/api/calls/', {'date_to': old_day})
        self.assertEqual([r['id'] for r in response.data['results']], [self.old_call.id])

    def test_summary_counts_the_filtered_calls(self):
        from apps.lead.models import LeadCall

        other = Lead.objects.create(full_name='Other Person', phone='9811111112')
        LeadCall.objects.create(
            lead=other, call_timestamp=self.recent_call.call_timestamp, direction='outbound',
            caller='Counselor Nine', phone_number='9811111112', duration_seconds=0,
            created_by=self.counselor, branch=self.branch,
        )
        data = self.client.get('/api/calls/summary/').data
        self.assertEqual(data, {
            'calls': 3, 'talk_seconds': 330, 'inbound': 1, 'outbound': 2,
            'leads': 2, 'average_seconds': 110,
        })
        data = self.client.get('/api/calls/summary/', {'direction': 'inbound'}).data
        self.assertEqual((data['calls'], data['talk_seconds'], data['leads']), (1, 60, 1))
        data = self.client.get('/api/calls/summary/', {'search': 'nobody'}).data
        self.assertEqual((data['calls'], data['average_seconds']), (0, 0))

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
        give_view_rights(other_admin)
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

    def _receipt_number(self, payment_id):
        nested = self.client.get(self.payments_url).data['results']
        listed = self.client.get('/api/payments/').data['results']
        numbers = {row['receipt_number'] for row in nested + listed if row['id'] == payment_id}
        self.assertEqual(len(numbers), 1)
        return numbers.pop()

    def test_a_new_receipt_after_the_old_one_is_cancelled(self):
        from apps.global_config.models import EmailLog

        payment_id = self._pay().data['id']
        url = f'{self.payments_url}{payment_id}/receipt/'
        old = Invoice.objects.get(payment_id=payment_id)
        self.assertEqual(self._receipt_number(payment_id), old.invoice_number)

        refused = self.client.post(url)
        self.assertEqual(refused.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(refused.data['detail'], 'This payment already has an active receipt.')

        self.client.post(f'/api/invoices/{old.id}/cancel/', {'reason': 'Wrong method'}, format='json')
        self.assertIsNone(self._receipt_number(payment_id))

        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertNotEqual(response.data['invoice_number'], old.invoice_number)
        self.assertEqual(response.data['amount'], '10000.00')
        self.assertEqual(self._receipt_number(payment_id), response.data['invoice_number'])
        # Sent as a receipt; the student got "payment received" once, with the first one.
        self.assertTrue(
            EmailLog.objects.filter(kind=EmailLog.Kind.INVOICE, invoice_id=response.data['id']).exists()
        )
        self.assertEqual(EmailLog.objects.filter(kind=EmailLog.Kind.PAYMENT_RECEIVED).count(), 1)
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.total_paid, 10000)

    def test_no_receipt_for_a_voided_or_pending_payment(self):
        voided = self._pay().data['id']
        self.client.post(f'{self.payments_url}{voided}/void/', {'reason': 'Duplicate entry'}, format='json')
        pending = self._pay(amount='5000', pay_status='pending').data['id']
        for payment_id in (voided, pending):
            response = self.client.post(f'{self.payments_url}{payment_id}/receipt/')
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertEqual(response.data['detail'], 'A receipt is made only for a successful payment.')
        self.assertFalse(Invoice.objects.filter(payment_id=pending).exists())

    def test_making_a_receipt_needs_change_payment(self):
        from apps.branch.models import Branch

        payment_id = self._pay().data['id']
        clerk = User.objects.create_user(
            email='clerk@test.com', name='Clerk', password='pass',
            branch=Branch.objects.create(name='Kathmandu', code='KTM'),
        )
        self.client.force_authenticate(clerk)
        response = self.client.post(f'{self.payments_url}{payment_id}/receipt/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


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

    def test_archive_and_restore_need_change_lead(self):
        from django.contrib.auth.models import Permission

        clerk = User.objects.create_user(email='clerk@test.com', name='Clerk', password='pass')
        self.client.force_authenticate(clerk)
        self.assertEqual(self.client.post(f'/api/leads/{self.lead.id}/archive/').status_code, 403)
        self.assertEqual(self.client.post(f'/api/leads/{self.lead.id}/restore/').status_code, 403)
        clerk.user_permissions.add(Permission.objects.get(codename='change_lead'))
        clerk = User.objects.get(pk=clerk.pk)
        self.client.force_authenticate(clerk)
        self.assertEqual(self.client.post(f'/api/leads/{self.lead.id}/archive/').status_code, 200)
        self.assertEqual(self.client.post(f'/api/leads/{self.lead.id}/restore/').status_code, 200)

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

    def _confirm(self, enrollment, paid='5000'):
        body = {'first_payment': {'amount': paid, 'payment_method': 'cash'}} if paid else {}
        return self.client.post(f'{self.enrollments_url}{enrollment.id}/confirm/', body, format='json')

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
        self.assertEqual(onward.status_code, status.HTTP_400_BAD_REQUEST)
        same = self.client.patch(url, {'status': 'confirmed', 'notes': 'x'}, format='json')
        self.assertEqual(same.status_code, status.HTTP_200_OK, same.data)

    def test_price_can_change_only_while_pending_and_unpaid(self):
        url = f'{self.enrollments_url}{self.enrollment.id}/'
        ok = self.client.patch(url, {'standard_discount': '2000', 'agreed_amount': '28000'}, format='json')
        self.assertEqual(ok.status_code, status.HTTP_200_OK, ok.data)
        Payment.objects.create(
            enrollment=self.enrollment, amount=1000, payment_date=timezone.now(),
            payment_method='cash', status='successful',
        )
        paid = self.client.patch(url, {'agreed_amount': '31000'}, format='json')
        self.assertEqual(paid.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('agreed_amount', paid.data)

    def test_confirm_needs_a_payment(self):
        response = self._confirm(self.enrollment, paid=None)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data['detail'], 'Record a payment first. An enrollment is confirmed once something is paid.',
        )
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, 'pending')
        self.assertFalse(Student.objects.exists())
        # A pending payment is not money yet.
        Payment.objects.create(
            enrollment=self.enrollment, amount=5000, payment_date=timezone.now(),
            payment_method='cash', status='pending',
        )
        self.assertEqual(self._confirm(self.enrollment, paid=None).status_code, 400)

    def test_confirm_takes_the_first_payment(self):
        response = self.client.post(f'{self.enrollments_url}{self.enrollment.id}/confirm/', {'first_payment': {
            'amount': '5000', 'payment_method': 'esewa', 'transaction_reference': 'ES-77',
        }}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['status'], 'confirmed')
        self.assertEqual(response.data['total_paid'], '5000.00')
        payment = Payment.objects.get(enrollment=self.enrollment)
        self.assertEqual(
            (payment.status, payment.payment_method, payment.transaction_reference, payment.received_by),
            ('successful', 'esewa', 'ES-77', self.admin),
        )
        receipt = payment.invoices.get()
        self.assertTrue(receipt.invoice_number.startswith('RCT-'))
        self.assertEqual(receipt.student, Student.objects.get())
        bad = self._enroll(course=Course.objects.create(name='Java', full_amount=1000).id, agreed_amount='1000')
        response = self._confirm(bad, paid='0')
        self.assertEqual(response.data['first_payment']['amount'], ['Amount must be more than 0.'])

    def test_a_payment_made_while_pending_counts(self):
        response = self.client.post(f'{self.enrollments_url}{self.enrollment.id}/payments/', {
            'amount': '3000', 'payment_date': timezone.now().isoformat(),
            'payment_method': 'cash', 'status': 'successful',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(self._confirm(self.enrollment, paid=None).status_code, 200)
        self.assertEqual(Payment.objects.filter(enrollment=self.enrollment).count(), 1)

    def test_a_free_enrollment_needs_no_payment(self):
        free = self._enroll(standard_discount='30000', agreed_amount='0')
        self.assertEqual(self._confirm(free, paid=None).status_code, 200)

    def test_price_is_fixed_once_confirmed(self):
        self._confirm(self.enrollment)
        response = self.client.patch(
            f'{self.enrollments_url}{self.enrollment.id}/', {'standard_discount': '1'}, format='json',
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


class AmountTypoTests(APITestCase):
    """A typo (100000 for a 10000 course) is refused before an invoice or receipt is made."""

    def setUp(self):
        self.admin = User.objects.create_user(
            email='typo@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.course = Course.objects.create(name='Excel', full_amount=10000)
        self.lead = Lead.objects.create(full_name='Hari KC', phone='9800000950')
        self.url = f'/api/leads/{self.lead.id}/enrollments/'

    def _enroll(self, **extra):
        return self.client.post(self.url, {
            'course': self.course.id, 'enrollment_date': str(date.today()), 'agreed_amount': '10000', **extra,
        }, format='json')

    def _pay(self, enrollment_id, amount, pay_status='successful'):
        return self.client.post(f'{self.url}{enrollment_id}/payments/', {
            'amount': amount, 'payment_date': timezone.now().isoformat(),
            'payment_method': 'cash', 'status': pay_status,
        }, format='json')

    def test_agreed_amount_and_discounts_stay_within_the_listed_fee(self):
        response = self._enroll(agreed_amount='100000')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['agreed_amount'], [
            "The agreed amount can't be more than NPR 10,000 (the listed fee). Add an extra charge for anything more.",
        ])
        response = self._enroll(standard_discount='100000', agreed_amount='0')
        self.assertEqual(response.data['standard_discount'], ['The discounts add up to more than the listed fee (NPR 10,000).'])
        response = self._enroll(standard_discount='2000', agreed_amount='9000')
        self.assertIn('NPR 8,000 (the listed fee less discounts)', response.data['agreed_amount'][0])
        self.assertEqual(self._enroll(agreed_amount='-1').data['agreed_amount'], ["The agreed amount can't be below 0."])
        self.assertEqual(self._enroll(standard_discount='2000', agreed_amount='8000').status_code, 201)
        self.assertEqual(Enrollment.objects.count(), 1)

    def test_a_payment_cannot_be_more_than_what_is_left(self):
        enrollment_id = self._enroll().data['id']
        response = self._pay(enrollment_id, '100000')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['amount'], ['This is more than what is left to pay (NPR 10,000). Check the amount.'])
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(Invoice.objects.exists())
        self.assertEqual(self._pay(enrollment_id, '6000').status_code, 201)
        self.assertEqual(self._pay(enrollment_id, '4000.01').data['amount'][0][:30], 'This is more than what is left')
        self.assertEqual(self._pay(enrollment_id, '4000').status_code, 201)
        self.assertEqual(self._pay(enrollment_id, '1').data['amount'], ['Nothing is left to pay on this enrollment.'])

    def test_pending_payments_count_as_on_their_way(self):
        enrollment_id = self._enroll().data['id']
        pending_id = self._pay(enrollment_id, '8000', pay_status='pending').data['id']
        self.assertIn('(NPR 2,000)', self._pay(enrollment_id, '5000').data['amount'][0])
        self.assertEqual(self._pay(enrollment_id, '2000').status_code, 201)
        # Confirming the pending one still fits: 8000 + 2000 = 10000.
        response = self.client.post(f'{self.url}{enrollment_id}/payments/{pending_id}/confirm/')
        self.assertEqual(response.status_code, 200, response.data)

    def test_confirming_a_pending_payment_over_what_is_left_is_refused(self):
        enrollment = Enrollment.objects.get(pk=self._enroll().data['id'])
        stale = Payment.objects.create(
            enrollment=enrollment, amount=9000, payment_date=timezone.now(),
            payment_method='cash', status='pending',
        )
        self.assertEqual(self._pay(enrollment.id, '1000').status_code, 201)
        Payment.objects.create(  # recorded outside the checks, e.g. old data
            enrollment=enrollment, amount=5000, payment_date=timezone.now(),
            payment_method='cash', status='successful',
        )
        response = self.client.post(f'{self.url}{enrollment.id}/payments/{stale.id}/confirm/')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['detail'], 'This is more than what is left to pay (NPR 4,000). Check the amount.')
        stale.refresh_from_db()
        self.assertEqual(stale.status, 'pending')

    def test_a_first_payment_over_the_fee_stops_the_confirm(self):
        enrollment_id = self._enroll().data['id']
        response = self.client.post(f'{self.url}{enrollment_id}/confirm/', {
            'first_payment': {'amount': '100000', 'payment_method': 'cash'},
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['detail'], 'This is more than what is left to pay (NPR 10,000). Check the amount.')
        self.assertEqual(Enrollment.objects.get(pk=enrollment_id).status, 'pending')
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(Student.objects.exists())

    def test_a_discount_entry_cannot_be_more_than_the_fee(self):
        enrollment_id = self._enroll().data['id']
        url = f'{self.url}{enrollment_id}/adjustments/'
        response = self.client.post(url, {'kind': 'discount', 'amount': '100000', 'reason': 'Typo'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data['detail'],
            "A discount can't be more than the fee (NPR 10,000 after earlier discounts and write-offs).",
        )
        self.assertEqual(self.client.post(url, {'kind': 'discount', 'amount': '3000', 'reason': 'Promo'}, format='json').status_code, 201)
        response = self.client.post(url, {'kind': 'discount', 'amount': '7001', 'reason': 'Again'}, format='json')
        self.assertIn('NPR 7,000', response.data['detail'])


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
        self.java_batch = CourseBatch.objects.create(
            name='Java Nov', course=self.other_course, capacity=2,
        )
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

    def _confirm(self, enrollment, paid='5000'):
        """Confirm with a first payment (something must be paid before confirming)."""
        body = {'first_payment': {'amount': paid, 'payment_method': 'cash'}} if paid else {}
        return self.client.post(self._url(enrollment, 'confirm/'), body, format='json')

    def _seats(self, batch):
        return self.client.get(f'/api/course-batches/{batch.id}/').data


class SeatTests(SeatTestBase):
    """Confirm takes a seat in the batch, cancel frees it, and full or closed batches refuse."""


    def test_listed_fee_is_the_course_price(self):
        lead = self._lead()
        body = {
            'course': self.course.id, 'enrollment_date': str(date.today()),
            'listed_fee': '1', 'agreed_amount': '30000',
        }
        created = self.client.post(f'/api/leads/{lead.id}/enrollments/', body, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data['listed_fee'], '30000.00')
        enrollment = Enrollment.objects.get(id=created.data['id'])

        typed = self.client.patch(self._url(enrollment), {'listed_fee': '5'}, format='json')
        self.assertEqual(typed.data['listed_fee'], '30000.00')

        moved = self.client.patch(
            self._url(enrollment), {'course': self.other_course.id, 'agreed_amount': '20000'}, format='json',
        )
        self.assertEqual(moved.data['listed_fee'], '20000.00')

        unpriced = Course.objects.create(name='Go', full_amount=None)
        refused = self.client.post(
            f'/api/leads/{lead.id}/enrollments/', {**body, 'course': unpriced.id}, format='json',
        )
        self.assertEqual(refused.status_code, 400)
        self.assertIn('no price', str(refused.data['course']))
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
        late = self._enroll(self.batch)  # picked while seats were free
        for _ in range(2):
            self.assertEqual(self._confirm(self._enroll(self.batch)).status_code, 200)

        response = self._confirm(late)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Python Nov is full (2 of 2 seats)', response.data['detail'])
        late.refresh_from_db()
        self.assertEqual(late.status, 'pending')
        self.assertIsNone(late.student)
        self.assertFalse(late.placements.exists())
        self.assertFalse(late.history.exists())
        self.assertEqual(Student.objects.count(), 2)
        # A fee invoice and a receipt for each of the two; the refused one keeps no payment.
        self.assertEqual(Invoice.objects.count(), 4)
        self.assertFalse(late.payments.exists())

    def test_a_full_batch_cannot_be_picked_for_a_new_enrollment(self):
        for _ in range(2):
            self.assertEqual(self._confirm(self._enroll(self.batch)).status_code, 200)
        response = self._enroll(self.batch, expect=status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data['batch'],
            ['Python Nov is full (2 of 2 seats). Pick another batch or add seats to it.'],
        )
        self._enroll(self.next_batch)

    def test_an_old_batch_with_no_seats_set_takes_no_one_until_seats_are_set(self):
        old = CourseBatch.objects.create(name='Python Old', course=self.course)
        response = self._enroll(old, expect=status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data['batch'],
            ['Python Old has no seats set. Edit the batch and set its seats first.'],
        )
        waiting = Enrollment.objects.create(
            lead=self._lead(), course=self.course, enrollment_date=date.today(),
            listed_fee=30000, agreed_amount=30000, batch=old,
        )
        response = self._confirm(waiting)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Python Old has no seats set', response.data['detail'])

        self.client.patch(f'/api/course-batches/{old.id}/', {'capacity': 10}, format='json')
        self.assertEqual(self._confirm(waiting).status_code, status.HTTP_200_OK)
        self.assertEqual(self._seats(old)['seats_left'], 9)

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
        response = self.client.post(
            self._url(enrollment, 'status/'), {'status': 'dropped_out', 'reason': 'Moved abroad'}, format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self._seats(self.batch)['seats_taken'], 0)
        self.assertEqual(enrollment.placements.order_by('-id').first().kind, Placement.Kind.LEAVE)

    def test_change_status_needs_a_reason_and_writes_history(self):
        enrollment = self._enroll(self.batch)
        url = self._url(enrollment, 'status/')
        self.assertEqual(self.client.post(url, {'status': 'active', 'reason': 'x'}, format='json').status_code, 400)
        self._confirm(enrollment)
        self.assertEqual(self.client.post(url, {'status': 'active'}, format='json').status_code, 400)
        self.assertEqual(
            self.client.post(url, {'status': 'cancelled', 'reason': 'x'}, format='json').status_code, 400,
        )
        response = self.client.post(url, {'status': 'active', 'reason': 'Classes started'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], 'active')
        self.assertEqual(response.data['current_batch'], self.batch.id)
        row = enrollment.history.get(action=EnrollmentHistory.Action.CHANGE_STATUS)
        self.assertEqual((row.before, row.after, row.reason), (
            {'status': 'confirmed'}, {'status': 'active'}, 'Classes started',
        ))
        self.assertEqual(self.client.post(url, {'status': 'active', 'reason': 'x'}, format='json').status_code, 400)

    def test_back_from_transferred_has_no_seat(self):
        enrollment = self._enroll(self.batch)
        self._confirm(enrollment)
        url = self._url(enrollment, 'status/')
        self.client.post(url, {'status': 'transferred', 'reason': 'Other branch'}, format='json')
        response = self.client.post(url, {'status': 'confirmed', 'reason': 'Came back'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        enrollment.refresh_from_db()
        self.assertEqual((enrollment.status, enrollment.current_batch_id), ('confirmed', None))

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
        self.assertEqual([r['enrollment'] for r in first['moved_out']], [mover.id])

    def test_old_batch_lists_who_moved_out_and_where_they_are_now(self):
        self._act('move', batch=self.next_batch.id, reason='Evening')
        old = self.client.get(f'/api/course-batches/{self.batch.id}/students/').data
        self.assertEqual(old['seated'], [])
        row = old['moved_out'][0]
        self.assertEqual(
            (row['enrollment'], row['how'], row['to_batch_name'], row['seat_state'],
             row['now_batch_name'], row['money_batch_name'], row['since']),
            (self.enrollment.id, 'move', 'Python Dec', 'seated', 'Python Dec', 'Python Nov', date.today()),
        )
        new = self.client.get(f'/api/course-batches/{self.next_batch.id}/students/').data
        self.assertEqual(new['moved_out'], [])

        # Paused out of the new batch: still listed on the old one, now paused.
        self._act('pause', reason='Exams')
        old = self.client.get(f'/api/course-batches/{self.batch.id}/students/').data
        self.assertEqual(
            [(r['seat_state'], r['now_batch_name']) for r in old['moved_out']], [('paused', None)],
        )
        self.assertEqual(old['paused'], [])

        # Back in the old batch: seated there, no longer moved out.
        self._act('resume', batch=self.batch.id, reason='Back')
        old = self.client.get(f'/api/course-batches/{self.batch.id}/students/').data
        self.assertEqual([r['enrollment'] for r in old['seated']], [self.enrollment.id])
        self.assertEqual(old['moved_out'], [])

    def test_enrollment_list_filters_by_seat(self):
        paused = self._enroll(self.batch)
        self._confirm(paused)
        self._act('pause', paused, reason='Exams')
        pending = self._enroll(self.batch)

        def ids(**params):
            data = self.client.get('/api/enrollments/', params).data
            return {r['id']: r for r in data['results']}

        rows = ids(seat='paused')
        self.assertEqual(list(rows), [paused.id])
        self.assertEqual(
            (rows[paused.id]['seat_state'], rows[paused.id]['paused_on'],
             rows[paused.id]['paused_from_name']),
            ('paused', str(date.today()), 'Python Nov'),
        )
        self.assertEqual(list(ids(seat='seated')), [self.enrollment.id])
        self.assertEqual(list(ids(seat='none')), [pending.id])
        everyone = ids()
        self.assertEqual(
            {k: v['seat_state'] for k, v in everyone.items()},
            {self.enrollment.id: 'seated', paused.id: 'paused', pending.id: 'none'},
        )
        self.assertIsNone(everyone[self.enrollment.id]['paused_on'])
        self.assertEqual(
            self.client.get('/api/enrollments/', {'seat': 'gone'}).status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_who_owes_filters_paused_students(self):
        paused = self._enroll(self.batch)
        self._confirm(paused)
        self._act('pause', paused, reason='Exams')

        everyone = self.client.get('/api/reports/who-owes/').data['results']
        self.assertEqual(
            {r['enrollment']: r['seat_state'] for r in everyone},
            {self.enrollment.id: 'seated', paused.id: 'paused'},
        )
        data = self.client.get('/api/reports/who-owes/', {'seat': 'paused'}).data
        self.assertEqual([r['enrollment'] for r in data['results']], [paused.id])
        self.assertEqual(data['results'][0]['paused_on'], str(date.today()))
        self.assertEqual(data['summary']['students'], 1)


class EnrollmentListTests(SeatTestBase):
    """GET /api/enrollments/ searches, filters and pages on the server, past 100 rows."""

    def _list(self, **params):
        response = self.client.get('/api/enrollments/', params)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response.data

    def test_search_finds_by_name_phone_course_and_batch(self):
        mina = self._enroll(self.batch, lead=Lead.objects.create(full_name='Mina Rai', phone='9811111111'))
        java = Enrollment.objects.create(
            lead=self._lead(), course=self.other_course, batch=self.java_batch,
            listed_fee=20000, agreed_amount=20000, enrollment_date=date.today(),
        )

        def ids(search):
            return [r['id'] for r in self._list(search=search)['results']]

        self.assertEqual(ids('mina'), [mina.id])
        self.assertEqual(ids('9811111111'), [mina.id])
        self.assertEqual(ids('Java'), [java.id])
        self.assertEqual(sorted(ids('Python Nov')), [mina.id])

    def test_search_works_with_status_and_seat(self):
        confirmed = self._enroll(self.batch)
        self._confirm(confirmed)
        self._enroll(self.batch)
        data = self._list(search='Python', status='confirmed', seat='seated')
        self.assertEqual([r['id'] for r in data['results']], [confirmed.id])

    def test_more_than_100_enrollments_page_through(self):
        for n in range(105):
            lead = Lead.objects.create(full_name=f'Bulk {n}', phone=f'97{n:08d}')
            Enrollment.objects.create(
                lead=lead, course=self.course, listed_fee=30000, agreed_amount=30000,
                enrollment_date=date.today(),
            )
        first = self._list(page_size=100, ordering='-created_at')
        self.assertEqual((first['count'], len(first['results']), first['total_pages']), (105, 100, 2))
        second = self._list(page_size=100, page=2, ordering='-created_at')
        self.assertEqual(len(second['results']), 5)
        seen = {r['id'] for r in first['results']} | {r['id'] for r in second['results']}
        self.assertEqual(len(seen), 105)


class FollowUpAndPaymentListTests(SeatTestBase):
    """The Follow-ups and Payments pages search, filter, count and total on the server."""

    def test_follow_ups_filter_open_and_due_by_nepal_day(self):
        from datetime import timedelta as td
        from apps.lead.filters import nepal_today_bounds

        start, end = nepal_today_bounds()
        lead = Lead.objects.create(full_name='Sita Kc', phone='9822222222')
        late = FollowUp.objects.create(lead=lead, scheduled_at=start - td(hours=1), purpose='Call back')
        today = FollowUp.objects.create(lead=lead, scheduled_at=start + td(hours=1), purpose='Fee chat')
        soon = FollowUp.objects.create(lead=lead, scheduled_at=end + td(hours=1), purpose='Demo')
        FollowUp.objects.create(
            lead=lead, scheduled_at=start - td(days=2), purpose='Done', status='completed',
        )

        def ids(**params):
            data = self.client.get('/api/followups/', {'open': 'true', **params}).data
            return [r['id'] for r in data['results']]

        self.assertEqual(ids(due='overdue'), [late.id])
        self.assertEqual(ids(due='today'), [today.id])
        self.assertEqual(ids(due='upcoming'), [soon.id])
        self.assertEqual(ids(search='fee'), [today.id])
        self.assertEqual(sorted(ids()), sorted([late.id, today.id, soon.id]))
        rows = self.client.get('/api/followups/', {'open': 'true', 'ordering': 'scheduled_at'}).data
        self.assertEqual([r['due'] for r in rows['results']], ['overdue', 'today', 'upcoming'])

        summary = self.client.get('/api/followups/summary/', {'open': 'true'}).data
        self.assertEqual(summary, {'overdue': 1, 'today': 1, 'upcoming': 1, 'total': 3})
        everything = self.client.get('/api/followups/summary/').data
        self.assertEqual(everything['overdue'], 2)

    def test_payments_search_and_summary_cover_every_page(self):
        enrollment = self._enroll(self.batch)
        for n in range(3):
            Payment.objects.create(
                enrollment=enrollment, amount=Decimal('1000'), payment_date=timezone.now(),
                payment_method='cash', status='successful', transaction_reference=f'REF-{n}',
            )
        Payment.objects.create(
            enrollment=enrollment, amount=Decimal('500'), payment_date=timezone.now(),
            payment_method='esewa', status='failed', transaction_reference='ESW-9',
        )
        page = self.client.get('/api/payments/', {'page_size': 2}).data
        self.assertEqual((page['count'], len(page['results'])), (4, 2))
        summary = self.client.get('/api/payments/summary/').data
        self.assertEqual(summary, {'count': 4, 'successful_total': '3000.00'})
        found = self.client.get('/api/payments/', {'search': 'ESW-9'}).data['results']
        self.assertEqual([r['transaction_reference'] for r in found], ['ESW-9'])
        cash = self.client.get('/api/payments/summary/', {'payment_method': 'cash'}).data
        self.assertEqual(cash, {'count': 3, 'successful_total': '3000.00'})


class DueDateTests(SeatTestBase):
    """Due dates: set at confirm, changed with a reason, filled oldest first."""

    def _day(self, days):
        return str(date.today() + timedelta(days=days))

    def _confirmed(self, due_dates=None, paid='1000'):
        enrollment = self._enroll()
        body = {'first_payment': {'amount': paid, 'payment_method': 'cash'}}
        if due_dates is not None:
            body['due_dates'] = due_dates
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
        self.assertEqual((rows[0]['status'], rows[0]['left']), ('part_paid', '29000.00'))
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
        self.assertEqual([r['status'] for r in rows], ['part_paid', 'due', 'due'])

    def test_parts_must_add_up_to_the_agreed_amount(self):
        enrollment = self._enroll()
        response = self.client.post(
            self._url(enrollment, 'confirm/'),
            {'due_dates': [{'due_on': self._day(0), 'amount': '20000'}],
             'first_payment': {'amount': '1000', 'payment_method': 'cash'}}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('add up to 20000', response.data['detail'])
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.status, 'pending')
        self.assertFalse(PaymentPlan.objects.exists())
        self.assertFalse(Student.objects.exists())

    def test_payments_fill_the_oldest_part_first(self):
        enrollment = self._confirmed(self._three_parts(), paid='15000')
        self._pay(enrollment, 5000, pay_status='pending')  # not money yet
        rows = self._rows(enrollment)
        self.assertEqual([r['status'] for r in rows], ['paid', 'part_paid', 'due'])
        self.assertEqual([r['left'] for r in rows], ['0.00', '5000.00', '10000.00'])

    def test_an_unpaid_part_past_its_due_date_is_late(self):
        enrollment = self._confirmed(paid='10000')
        response = self._change(enrollment, self._three_parts(first=-40))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
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
        enrollment = self._confirmed(self._three_parts(), paid='10000')
        url = self._url(enrollment, 'adjustments/')
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
        self.assertEqual(self._rows(enrollment)[0]['status'], 'part_paid')

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

    def test_a_changed_plan_does_not_count_payments_twice(self):
        # The old plan is kept when due dates change; 4000 paid must stay 4000.
        enrollment = self._enrollment([(0, 10000)], paid=4000)
        plan = PaymentPlan.objects.create(enrollment=enrollment, reason='New dates', created_by=self.counselor)
        Installment.objects.create(plan=plan, due_on=self.TODAY, amount=10000)
        report = self._run()
        self.assertEqual([r['enrollment'] for r in report], [enrollment.id])
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
    """Teams accounts are given to seated students, each with a state, and taken back with a reason."""

    URL = '/api/teams-licences/'
    ACCOUNTS = '/api/teams-accounts/'

    def setUp(self):
        from apps.branch.models import Branch

        self.admin = User.objects.create_user(
            email='teams@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.ktm = Branch.objects.create(name='Kathmandu', code='TKTM')
        self.pkr = Branch.objects.create(name='Pokhara', code='TPKR')
        self.course = Course.objects.create(name='Python', full_amount=30000)
        self.batch = CourseBatch.objects.create(
            name='Python Nov', course=self.course, status='running', capacity=10,
        )
        self.people = 0

    def _account(self, email, branch=None):
        return TeamsAccount.objects.create(email=email, branch=branch, created_by=self.admin)

    def _student(self, *, seated=True, status='confirmed', branch=None, email=''):
        self.people += 1
        lead = Lead.objects.create(
            full_name=f'Teams {self.people}', phone=f'98000040{self.people:02d}', email=email,
            branch=branch,
        )
        return Enrollment.objects.create(
            lead=lead, course=self.course, enrollment_date=timezone.localdate(),
            listed_fee=30000, agreed_amount=30000, status=status, branch=branch,
            batch=self.batch, current_batch=self.batch if seated else None,
        )

    def _give(self, enrollment, account, **extra):
        return self.client.post(
            self.URL, {'enrollment': enrollment.id, 'teams_account': account.id, **extra}, format='json',
        )

    def _free(self, licence_id, reason='Course over'):
        return self.client.post(f'{self.URL}{licence_id}/free/', {'reason': reason}, format='json')

    def _summary(self, **params):
        return self.client.get(f'{self.ACCOUNTS}summary/', params).data

    def _rows(self, **params):
        return {r['email']: r for r in self.client.get(self.ACCOUNTS, params).data}

    def test_give_and_take_back_a_licence(self):
        account = self._account('Teams1@SkillShikshya.com')
        self._account('teams2@skillshikshya.com')
        student = self._student()
        response = self._give(student, account)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['account'], 'teams1@skillshikshya.com')
        self.assertEqual(response.data['batch_name'], 'Python Nov')
        self.assertEqual(response.data['state']['state'], 'in_use')
        self.assertEqual(self._summary(), {
            'total': 2, 'ready': 1, 'in_use': 1, 'safe_to_remove': 0, 'action_required': 0, 'retired': 0,
        })
        row = self._rows()['teams1@skillshikshya.com']
        self.assertEqual(row['state'], 'in_use')
        self.assertEqual(row['licence']['name'], 'Teams 1')
        self.assertEqual(row['licence']['batch_name'], 'Python Nov')

        lead_url = f'/api/leads/{student.lead_id}/enrollments/{student.id}/'
        teams = self.client.get(lead_url).data['teams_licence']
        self.assertEqual((teams['account'], teams['state']), ('teams1@skillshikshya.com', 'in_use'))

        licence_id = response.data['id']
        self.assertEqual(self._free(licence_id, reason=' ').status_code, 400)
        response = self._free(licence_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['release_reason'], 'Course over')
        self.assertEqual(response.data['released_by_name'], 'Admin')
        self.assertEqual(self._free(licence_id).data['detail'], 'This licence was already taken back.')
        self.assertIsNone(self.client.get(lead_url).data['teams_licence'])
        self.assertEqual(self._rows()['teams1@skillshikshya.com']['state'], 'ready')

        actions = list(student.history.order_by('created_at', 'id').values_list('action', flat=True))
        self.assertEqual(actions, ['give_licence', 'free_licence'])

        # The same account can be given again once it is back.
        self.assertEqual(self._give(student, account).status_code, 201)

    def test_one_licence_per_student_and_per_account(self):
        one, two = self._account('t1@x.com'), self._account('t2@x.com')
        a, b = self._student(), self._student()
        self.assertEqual(self._give(a, one).status_code, 201)
        self.assertEqual(self._give(a, two).data['detail'], 'This student already has a Teams licence.')
        response = self._give(b, one)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['detail'], 't1@x.com is in use by Teams 1. Take it back first.')

    def test_retired_accounts_are_not_given_or_counted(self):
        account = self._account('old@x.com')
        self._account('new@x.com')
        response = self.client.post(f'{self.ACCOUNTS}{account.id}/retire/', {'reason': 'Removed'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data['state'], response.data['reason']), ('retired', 'Removed'))
        self.assertEqual(self._give(self._student(), account).data['detail'], 'old@x.com is retired. Pick another account.')
        self.assertEqual(self._summary()['total'], 1)
        self.assertEqual(self._summary()['retired'], 1)

        response = self.client.post(f'{self.ACCOUNTS}{account.id}/restore/')
        self.assertEqual(response.data['state'], 'ready')
        self.assertEqual(self._summary()['total'], 2)

    def test_an_account_in_use_cannot_be_retired(self):
        account = self._account('busy@x.com')
        self._give(self._student(), account)
        response = self.client.post(f'{self.ACCOUNTS}{account.id}/retire/', {'reason': 'x'}, format='json')
        self.assertEqual(response.data['detail'], 'Take this account back from the student first.')

    def test_add_accounts_in_bulk(self):
        self._account('have@x.com')
        response = self.client.post(self.ACCOUNTS, {
            'emails': ['A@x.com', 'a@x.com', 'b@x.com', 'have@x.com'], 'branch': self.ktm.id,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data, {'added': ['a@x.com', 'b@x.com'], 'existing': ['have@x.com']})
        self.assertEqual(TeamsAccount.objects.get(email='a@x.com').branch, self.ktm)
        self.assertEqual(self.client.post(self.ACCOUNTS, {'emails': ['nope']}, format='json').status_code, 400)

    def test_only_seated_students_get_one(self):
        account = self._account('a@x.com')
        message = 'Only a student sitting in a batch can get a Teams licence.'
        self.assertEqual(self._give(self._student(seated=False), account).data['detail'], message)
        self.assertEqual(
            self._give(self._student(seated=False, status='pending'), account).data['detail'], message,
        )

    def test_state_follows_the_student(self):
        from apps.course.services import close_batch

        cases = {
            'done@x.com': ('completed', 'safe_to_remove', 'Course completed'),
            'drop@x.com': ('dropped_out', 'action_required', 'Dropped out'),
            'moved@x.com': ('transferred', 'action_required', 'Transferred'),
        }
        for email, (status_, _, _) in cases.items():
            student = self._student()
            self._give(student, self._account(email))
            Enrollment.objects.filter(pk=student.pk).update(status=status_)
        rows = self._rows()
        for email, (_, state, reason) in cases.items():
            self.assertEqual((rows[email]['state'], rows[email]['reason']), (state, reason), email)

        # Cancelling and closing the batch keep the licence and ask someone to act.
        cancelled, studying = self._student(), self._student()
        self._give(cancelled, self._account('cancel@x.com'))
        self._give(studying, self._account('class@x.com'))
        services.cancel_enrollment(cancelled, reason='Moved abroad', user=self.admin)
        rows = self._rows()
        self.assertEqual((rows['cancel@x.com']['state'], rows['cancel@x.com']['reason']),
                         ('action_required', 'Enrollment cancelled'))
        self.assertEqual(rows['class@x.com']['state'], 'in_use')

        # Closing the batch marks the student completed: the licence is safe to remove.
        close_batch(self.batch, reason='Finished', user=self.admin, close_anyway=True)
        rows = self._rows()
        self.assertEqual((rows['class@x.com']['state'], rows['class@x.com']['reason']),
                         ('safe_to_remove', 'Course completed'))
        self.assertFalse(TeamsLicence.objects.filter(released_at__isnull=False).exists())
        self.assertEqual(self._summary(), {
            'total': 5, 'ready': 0, 'in_use': 0, 'safe_to_remove': 2, 'action_required': 3, 'retired': 0,
        })
        filtered = self.client.get(self.ACCOUNTS, {'state': 'safe_to_remove'}).data
        self.assertCountEqual([r['email'] for r in filtered], ['done@x.com', 'class@x.com'])

    def test_a_student_still_seated_in_a_closed_batch_needs_action(self):
        """Closing completes everyone, but a student set back to active later shows Batch closed."""
        from apps.course.services import close_batch

        studying = self._student()
        self._give(studying, self._account('late@x.com'))
        close_batch(self.batch, reason='Finished', user=self.admin, close_anyway=True)
        Enrollment.objects.filter(pk=studying.pk).update(status='active')
        row = self._rows()['late@x.com']
        self.assertEqual((row['state'], row['reason']), ('action_required', 'Batch closed'))

    def test_pause_asks_for_action_and_resume_puts_it_back(self):
        account = self._account('kept@x.com')
        student, other = self._student(), self._student()
        self._give(student, account)
        services.pause_enrollment(student, reason='Exams', user=self.admin)

        licence = TeamsLicence.objects.get(enrollment=student)
        self.assertTrue(licence.is_open)
        self.assertIsNotNone(licence.paused_at)
        row = self._rows()['kept@x.com']
        self.assertEqual((row['state'], row['reason']), ('action_required', 'Paused'))
        lead_url = f'/api/leads/{student.lead_id}/enrollments/{student.id}/'
        self.assertEqual(self.client.get(lead_url).data['teams_licence']['reason'], 'Paused')
        # The paused licence is still the student's: its account can't go to someone else.
        self.assertEqual(self._give(other, account).status_code, 400)

        services.resume_enrollment(student, self.batch, reason='Back', user=self.admin)
        licence.refresh_from_db()
        self.assertIsNone(licence.paused_at)
        self.assertEqual(self._rows()['kept@x.com']['state'], 'in_use')
        actions = list(student.history.order_by('created_at', 'id').values_list('action', 'reason'))
        self.assertIn(('pause_licence', 'Paused: Exams'), actions)
        self.assertIn(('resume_licence', 'Resumed: Back'), actions)

    def test_a_paused_licence_can_still_be_taken_back(self):
        student = self._student()
        licence_id = self._give(student, self._account('gone@x.com')).data['id']
        services.pause_enrollment(student, reason='Long break', user=self.admin)
        self.assertEqual(self._free(licence_id, reason='Not coming back soon').status_code, 200)
        services.resume_enrollment(student, self.batch, reason='Back', user=self.admin)
        self.assertFalse(TeamsLicence.objects.filter(released_at__isnull=True).exists())

    def test_sign_in_details_are_emailed_and_the_password_is_not_kept(self):
        from django.core import mail
        from apps.global_config.models import EmailLog

        student = self._student(email='learner@example.com')
        account = self._account('learner1@skillshikshya.com')
        self.assertEqual(
            self._give(student, account, send_details=True).data['password'], ['Enter the password to send.'],
        )
        response = self._give(student, account, password='Temp#4821', send_details=True)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['email']['status'], 'sent')
        self.assertEqual(response.data['details_sent_to'], 'learner@example.com')
        self.assertIsNotNone(response.data['details_sent_at'])

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, ['learner@example.com'])
        self.assertIn('learner1@skillshikshya.com', sent.body)
        self.assertIn('Temp#4821', sent.body)
        self.assertIn('Temp#4821', sent.alternatives[0][0])

        log = EmailLog.objects.get(kind=EmailLog.Kind.TEAMS_ACCOUNT)
        self.assertEqual(log.status, EmailLog.Status.SENT)
        self.assertNotIn('Temp#4821', log.body_html + log.body_text)
        self.assertIn('learner1@skillshikshya.com', log.body_text)

        # Sent again later, e.g. after a password reset in Microsoft 365.
        response = self.client.post(
            f'{self.URL}{response.data["id"]}/send-details/', {'password': 'New#9911'}, format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn('New#9911', mail.outbox[1].body)

    def test_no_email_address_means_nothing_is_sent(self):
        from django.core import mail

        response = self._give(self._student(), self._account('x1@x.com'), password='abc', send_details=True)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['email']['status'], 'skipped')
        self.assertIsNone(response.data['details_sent_at'])
        self.assertEqual(len(mail.outbox), 0)

    def test_branches_see_their_accounts_and_the_shared_ones(self):
        self._account('ktm@x.com', self.ktm)
        pkr_account = self._account('pkr@x.com', self.pkr)
        shared = self._account('shared@x.com')
        pkr_student = self._student(branch=self.pkr)
        self._give(pkr_student, shared)

        clerk = User.objects.create_user(
            email='teams-ktm@test.com', name='KTM', password='pass', branch=self.ktm,
        )
        give_view_rights(clerk)
        self.client.force_authenticate(clerk)
        rows = self._rows()
        self.assertEqual(set(rows), {'ktm@x.com', 'shared@x.com'})
        # In use at another branch: no student details.
        self.assertEqual(rows['shared@x.com']['licence'], {'id': rows['shared@x.com']['licence']['id'], 'other_branch': True})
        self.assertEqual(self._summary()['total'], 2)

        self.client.force_authenticate(self.admin)
        ktm_student = self._student(branch=self.ktm)
        response = self._give(ktm_student, pkr_account)
        self.assertEqual(response.data['detail'], 'pkr@x.com belongs to Pokhara. Pick an account of this branch.')
        self.assertEqual(
            [r['email'] for r in self.client.get(self.ACCOUNTS, {'give_at': self.ktm.id, 'state': 'ready'}).data],
            ['ktm@x.com'],
        )

    def test_list_open_licences(self):
        a, b = self._student(), self._student()
        first = self._give(a, self._account('a@x.com')).data['id']
        self._give(b, self._account('b@x.com'))
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

    def test_giving_and_adding_need_permission(self):
        clerk = User.objects.create_user(email='teams-clerk@test.com', name='Clerk', password='pass')
        self.client.force_authenticate(clerk)
        self.assertEqual(self._give(self._student(), self._account('q@x.com')).status_code, 403)
        self.assertEqual(self.client.post(self.ACCOUNTS, {'emails': ['n@x.com']}, format='json').status_code, 403)
        # Reading needs the view rights.
        self.assertEqual(self.client.get(self.URL).status_code, 403)
        self.assertEqual(self.client.get(self.ACCOUNTS).status_code, 403)
        clerk = give_rights(clerk, 'lead.view_teamslicence', 'lead.view_teamsaccount')
        self.client.force_authenticate(clerk)
        self.assertEqual(self.client.get(self.URL).status_code, 200)
        self.assertEqual(self.client.get(self.ACCOUNTS).status_code, 200)


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

    @override_settings(LEAD_INTAKE_TOKEN='t')
    def test_intake_adds_one_enquiry_per_course(self):
        self.client.credentials(HTTP_X_INTAKE_TOKEN='t')
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


class PhoneMatchTests(APITestCase):
    """The same number typed any way finds the same lead, in any branch."""

    URL = '/api/leads/phone-match/'

    def setUp(self):
        from apps.branch.models import Branch

        self.ktm = Branch.objects.create(name='Kathmandu', code='KTM')
        self.pkr = Branch.objects.create(name='Pokhara', code='PKR')
        self.admin = User.objects.create_user(
            email='phone@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.course = Course.objects.create(name='Python', full_amount=30000)
        self.lead = Lead.objects.create(
            full_name='Hari Gurung', phone='+977 980-000-0800', branch=self.ktm,
        )

    def _match(self, phone, **params):
        response = self.client.get(self.URL, {'phone': phone, **params})
        self.assertEqual(response.status_code, 200)
        return [row['id'] for row in response.data]

    def test_phone_key(self):
        from apps.lead.phones import phone_key

        self.assertEqual(phone_key('+977 980-000-0800'), '9800000800')
        self.assertEqual(phone_key('9779800000800'), '9800000800')
        self.assertEqual(phone_key('980 000 0800'), '9800000800')
        self.assertEqual(phone_key('01-4412345'), '014412345')
        self.assertEqual(phone_key(None), '')

    def test_finds_the_lead_however_the_number_is_typed(self):
        for typed in ['9800000800', '+9779800000800', '980-000-0800']:
            self.assertEqual(self._match(typed), [self.lead.id], typed)
        self.assertEqual(self._match('9800000801'), [])
        self.assertEqual(self._match('0800'), [])

    def test_second_phone_and_exclude_and_deleted(self):
        other = Lead.objects.create(full_name='Other', phone='9811111111', secondary_phone='9800000800')
        self.assertEqual(sorted(self._match('9800000800')), sorted([self.lead.id, other.id]))
        self.assertEqual(self._match('9800000800', exclude=other.id), [self.lead.id])
        other.is_deleted = True
        other.save()
        self.assertEqual(self._match('9800000800'), [self.lead.id])

    def test_match_shows_the_enquiries(self):
        from apps.lead.enquiries import add_enquiry

        add_enquiry(self.lead, course=self.course, user=self.admin)
        row = self.client.get(self.URL, {'phone': '9800000800'}).data[0]
        self.assertEqual(row['branch_name'], 'Kathmandu')
        self.assertEqual(
            [(e['course_name'], e['stage']) for e in row['enquiries']], [('Python', 'new')],
        )

    def test_new_lead_with_a_known_phone_is_refused_with_the_match(self):
        body = {'full_name': 'Hari G', 'phone': '9779800000800'}
        response = self.client.post('/api/leads/', body, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['phone'], ['A lead with this phone number already exists.'])
        self.assertEqual([m['id'] for m in response.data['phone_match']], [self.lead.id])
        self.assertEqual(Lead.objects.count(), 1)

        response = self.client.post(
            '/api/leads/', {**body, 'allow_duplicate_phone': True}, format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Lead.objects.count(), 2)

    def test_other_branch_finds_the_lead_and_sees_it_after_taking_an_enquiry(self):
        from apps.lead.enquiries import add_enquiry

        counselor = User.objects.create_user(
            email='pkr@test.com', name='Pokhara Counselor', password='pass', branch=self.pkr,
        )
        give_view_rights(counselor)
        self.client.force_authenticate(counselor)
        self.assertEqual(self._match('9800000800'), [self.lead.id])
        self.assertEqual(self.client.get(f'/api/leads/{self.lead.id}/').status_code, 404)

        enquiry = add_enquiry(self.lead, course=self.course, user=counselor)
        self.assertEqual(enquiry.branch, self.pkr)
        self.assertEqual(self.client.get(f'/api/leads/{self.lead.id}/').status_code, 200)
        listed = self.client.get('/api/leads/').data['results']
        self.assertEqual([row['id'] for row in listed], [self.lead.id])

    @override_settings(LEAD_INTAKE_TOKEN='t')
    def test_intake_from_a_known_number_adds_to_the_same_lead(self):
        self.client.credentials(HTTP_X_INTAKE_TOKEN='t')
        response = self.client.post('/api/public/lead-intake/', {
            'name': 'Hari', 'phone_number': '980-000-0800', 'course_interests': 'Python',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data['id'], response.data['existing']), (self.lead.id, True))
        self.assertEqual(Lead.objects.count(), 1)
        self.assertEqual(list(self.lead.course_interests.values_list('course__name', 'stage')), [('Python', 'new')])
        # Asking again for the same course does not add a second open enquiry.
        self.client.credentials(HTTP_X_INTAKE_TOKEN='t')
        self.client.post('/api/public/lead-intake/', {
            'name': 'Hari', 'phone_number': '9800000800', 'course_interests': 'Python',
        }, format='json')
        self.assertEqual(self.lead.course_interests.count(), 1)



class VoiceNoteTests(APITestCase):
    """Voice notes on an enquiry: private recordings, removed with the row or the lead."""

    def setUp(self):
        import shutil
        import tempfile

        from django.test import override_settings

        from apps.branch.models import Branch

        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        settings_override = override_settings(MEDIA_ROOT=media, R2_BUCKET_NAME='', R2_ACCESS_KEY_ID='')
        settings_override.enable()
        self.addCleanup(settings_override.disable)

        # The file delete is a Celery job; run it here instead of sending it to Redis.
        from unittest import mock

        from apps.lead.tasks import delete_voice_note_file_task

        run_now = mock.patch.object(
            delete_voice_note_file_task, 'delay',
            side_effect=lambda key: delete_voice_note_file_task.apply(args=[key]),
        )
        run_now.start()
        self.addCleanup(run_now.stop)

        self.ktm = Branch.objects.create(name='Kathmandu', code='KTM')
        self.pkr = Branch.objects.create(name='Pokhara', code='PKR')
        self.counselor = User.objects.create_user(
            email='voice@test.com', name='Sita', password='pass', branch=self.ktm,
        )
        from django.contrib.auth.models import Permission

        self.counselor.user_permissions.add(Permission.objects.get(codename='change_leadcourseinterest'))
        self.counselor.user_permissions.add(Permission.objects.get(codename='view_voicenote'))
        self.client.force_authenticate(self.counselor)
        self.course = Course.objects.create(name='Python', full_amount=30000)
        self.lead = Lead.objects.create(full_name='Ram Thapa', phone='9800000900', branch=self.ktm)
        self.enquiry = LeadCourseInterest.objects.create(lead=self.lead, course=self.course, branch=self.ktm)

    def _url(self, lead=None, rest=''):
        return f'/api/leads/{(lead or self.lead).id}/voice-notes/{rest}'

    def _record(self, expect=201, content_type='audio/webm;codecs=opus', body=b'voice-bytes', **extra):
        from django.core.files.uploadedfile import SimpleUploadedFile

        data = {
            'file': SimpleUploadedFile('note.webm', body, content_type=content_type),
            'enquiry': self.enquiry.id, 'duration_seconds': 42, **extra,
        }
        response = self.client.post(self._url(), data, format='multipart')
        self.assertEqual(response.status_code, expect, response.data)
        return response.data

    def _file_exists(self, note_id):
        from django.core.files.storage import default_storage

        return default_storage.exists(VoiceNote.objects.get(pk=note_id).file_key)

    def test_record_and_play(self):
        data = self._record(note='Asked about weekend classes')
        self.assertEqual(
            (data['content_type'], data['size_bytes'], data['duration_seconds'], data['course_name']),
            ('audio/webm', 11, 42, 'Python'),
        )
        self.assertEqual((data['created_by_name'], data['branch']), ('Sita', self.ktm.id))
        self.assertNotIn('file', data)
        audio = self.client.get(self._url(rest=f'{data["id"]}/audio/'))
        self.assertEqual(audio.status_code, 200)
        self.assertEqual(audio['Content-Type'], 'audio/webm')
        self.assertEqual(audio.content, b'voice-bytes')
        listed = self.client.get(self._url(), {'enquiry': self.enquiry.id}).data['results']
        self.assertEqual([n['id'] for n in listed], [data['id']])

    def test_only_sound_and_sensible_sizes_are_saved(self):
        refused = self._record(expect=400, content_type='application/pdf')
        self.assertEqual(str(refused['file'][0]), 'Only a sound recording can be saved.')
        refused = self._record(expect=400, duration_seconds=11 * 60)
        self.assertEqual(str(refused['duration_seconds'][0]), 'A voice note can be 10 minutes at most.')
        other_lead = Lead.objects.create(full_name='Hari', phone='9800000901', branch=self.ktm)
        other = LeadCourseInterest.objects.create(lead=other_lead, course=self.course)
        refused = self._record(expect=400, enquiry=other.id)
        self.assertEqual(str(refused['enquiry'][0]), 'This enquiry belongs to another lead.')
        self.assertFalse(VoiceNote.objects.exists())

    def test_recording_needs_enquiry_rights(self):
        viewer = User.objects.create_user(email='view@test.com', name='Viewer', password='pass', branch=self.ktm)
        self.client.force_authenticate(viewer)
        self._record(expect=403)

    def test_another_branch_cannot_hear_it(self):
        note = self._record()
        outsider = User.objects.create_user(email='pkr@test.com', name='Pkr', password='pass', branch=self.pkr)
        give_view_rights(outsider)
        self.client.force_authenticate(outsider)
        self.assertEqual(self.client.get(self._url(rest=f'{note["id"]}/audio/')).status_code, 404)
        self.assertEqual(self.client.get(self._url()).status_code, 404)

    def test_only_the_recorder_or_staff_deletes_and_the_file_goes(self):
        note = self._record()
        self.assertTrue(self._file_exists(note['id']))
        key = VoiceNote.objects.get(pk=note['id']).file_key
        colleague = User.objects.create_user(email='col@test.com', name='Col', password='pass', branch=self.ktm)
        self.client.force_authenticate(colleague)
        refused = self.client.delete(self._url(rest=f'{note["id"]}/'))
        self.assertEqual(refused.status_code, 403)
        self.client.force_authenticate(self.counselor)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.delete(self._url(rest=f'{note["id"]}/')).status_code, 204)
        from django.core.files.storage import default_storage

        self.assertFalse(default_storage.exists(key))

    def test_deleting_the_lead_forever_removes_its_recordings(self):
        from django.core.files.storage import default_storage

        note = self._record()
        key = VoiceNote.objects.get(pk=note['id']).file_key
        with self.captureOnCommitCallbacks(execute=True):
            self.lead.delete()
        self.assertFalse(VoiceNote.objects.exists())
        self.assertFalse(default_storage.exists(key))



class VoiceNoteFileDeleteJobTests(APITestCase):
    """The recording is deleted by the Celery worker, after the row delete is saved."""

    def setUp(self):
        from unittest import mock

        from apps.lead.tasks import delete_voice_note_file_task

        self.mock = mock
        self.task = delete_voice_note_file_task
        self.admin = User.objects.create_user(
            email='admin@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.lead = Lead.objects.create(full_name='Ram Thapa', phone='9800000950')
        self.notes = [
            VoiceNote.objects.create(
                lead=self.lead, file_key=f'voice-notes/{self.lead.id}/{name}.webm',
                content_type='audio/webm', size_bytes=10, created_by=self.admin,
            )
            for name in ('one', 'two')
        ]
        queue = mock.patch.object(delete_voice_note_file_task, 'delay')
        self.delay = queue.start()
        self.addCleanup(queue.stop)

    def keys(self):
        return sorted(n.file_key for n in self.notes)

    def queued(self):
        return sorted(c.args[0] for c in self.delay.call_args_list)

    def test_deleting_a_note_queues_one_job_after_the_delete_is_saved(self):
        with self.captureOnCommitCallbacks(execute=False) as waiting:
            res = self.client.delete(f'/api/leads/{self.lead.id}/voice-notes/{self.notes[0].id}/')
            self.assertEqual(res.status_code, 204)
            self.delay.assert_not_called()  # nothing is queued before the commit
        for callback in waiting:
            callback()
        self.delay.assert_called_once_with(self.notes[0].file_key)

    def test_deleting_the_lead_forever_queues_one_job_per_note(self):
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.post(f'/api/leads/{self.lead.id}/purge/')
        self.assertEqual(res.status_code, 204)
        self.assertFalse(VoiceNote.objects.exists())
        self.assertEqual(self.queued(), self.keys())

    def test_recycle_bin_keeps_the_recordings(self):
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.delete(f'/api/leads/{self.lead.id}/')
        self.assertEqual(res.status_code, 204)
        self.assertEqual(VoiceNote.objects.count(), 2)
        self.delay.assert_not_called()

    def test_a_rolled_back_delete_queues_nothing(self):
        from django.db import transaction

        note_id = self.notes[0].pk  # delete() clears pk on the instance
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    self.notes[0].delete()
                    raise RuntimeError('something else failed')
            except RuntimeError:
                pass
        self.assertTrue(VoiceNote.objects.filter(pk=note_id).exists())
        self.delay.assert_not_called()

    def test_redis_down_deletes_the_file_now(self):
        self.delay.side_effect = ConnectionError('Redis is down')
        key = self.notes[0].file_key
        with self.mock.patch('apps.lead.voice_notes.delete_file') as delete_file, \
                self.assertLogs('apps.lead.voice_notes', level='WARNING') as logs, \
                self.captureOnCommitCallbacks(execute=True):
            res = self.client.delete(f'/api/leads/{self.lead.id}/voice-notes/{self.notes[0].id}/')
        self.assertEqual(res.status_code, 204)
        delete_file.assert_called_once_with(key)
        self.assertIn('deleting it now', logs.output[0])

    def test_the_job_retries_until_r2_answers(self):
        with self.mock.patch('apps.lead.voice_notes.delete_file',
                             side_effect=[ConnectionError('R2 down'), TimeoutError('slow'), None]) as delete_file:
            result = self.task.apply(args=['voice-notes/1/x.webm'])
        self.assertTrue(result.successful())
        self.assertEqual(delete_file.call_count, 3)

    def test_the_job_gives_up_after_its_last_try(self):
        with self.mock.patch('apps.lead.voice_notes.delete_file', side_effect=ConnectionError('R2 down')) as delete_file:
            result = self.task.apply(args=['voice-notes/1/x.webm'])
        self.assertTrue(result.failed())
        self.assertEqual(delete_file.call_count, 1 + self.task.max_retries)

    def test_running_the_job_twice_is_harmless(self):
        import shutil
        import tempfile

        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage
        from django.test import override_settings

        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        with override_settings(MEDIA_ROOT=media, R2_BUCKET_NAME='', R2_ACCESS_KEY_ID=''):
            key = default_storage.save('voice-notes/1/twice.webm', ContentFile(b'abc'))
            self.assertTrue(self.task.apply(args=[key]).successful())
            self.assertTrue(self.task.apply(args=[key]).successful())
            self.assertFalse(default_storage.exists(key))


class VoiceNoteSweepTests(APITestCase):
    """Nightly clean-up of recordings that no voice note points to."""

    def setUp(self):
        import shutil
        import tempfile

        from django.test import override_settings

        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        local = override_settings(
            MEDIA_ROOT=media, R2_ACCOUNT_ID='', R2_ACCESS_KEY_ID='', R2_SECRET_ACCESS_KEY='', R2_BUCKET_NAME='',
        )
        local.enable()
        self.addCleanup(local.disable)
        self.lead = Lead.objects.create(full_name='Ram Thapa', phone='9800000960')

    def _file(self, key, hours_old):
        import os
        from datetime import timedelta

        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        name = default_storage.save(key, ContentFile(b'audio'))
        stamp = (timezone.now() - timedelta(hours=hours_old)).timestamp()
        os.utime(default_storage.path(name), (stamp, stamp))
        return name

    def _note(self, key, lead=None):
        return VoiceNote.objects.create(lead=lead or self.lead, file_key=key, content_type='audio/webm', size_bytes=5)

    def _exists(self, key):
        from django.core.files.storage import default_storage

        return default_storage.exists(key)

    def _sweep(self, **kwargs):
        from apps.lead.voice_sweep import sweep_orphan_files

        return sweep_orphan_files(**kwargs)

    def test_only_old_files_with_no_voice_note_go(self):
        in_use = self._file('voice-notes/1/in-use.webm', 48)
        self._note(in_use)
        binned_lead = Lead.objects.create(full_name='In the bin', phone='9800000961', is_deleted=True)
        in_bin = self._file('voice-notes/2/in-bin.webm', 48)
        self._note(in_bin, lead=binned_lead)
        orphan = self._file('voice-notes/1/orphan.webm', 48)
        fresh = self._file('voice-notes/3/fresh.webm', 2)
        other_folder = self._file('uploads/old.pdf', 48)

        result = self._sweep()

        self.assertEqual(
            (result.checked, result.kept, result.too_new, result.deleted, result.stopped),
            (4, 2, 1, 1, False),
        )
        self.assertEqual([k for k, _ in result.orphans], [orphan])
        self.assertFalse(self._exists(orphan))
        for key in (in_use, in_bin, fresh, other_folder):
            self.assertTrue(self._exists(key), key)
        self.assertIn('Local media folder: checked 4 files', result.summary())

    def test_dry_run_deletes_nothing(self):
        orphan = self._file('voice-notes/1/orphan.webm', 48)
        result = self._sweep(dry_run=True)
        self.assertEqual((len(result.orphans), result.deleted), (1, 0))
        self.assertTrue(self._exists(orphan))

    def test_safety_stop_when_most_files_look_orphaned(self):
        for i in range(3):
            self._note(self._file(f'voice-notes/1/kept-{i}.webm', 48))
        orphans = [self._file(f'voice-notes/9/orphan-{i}.webm', 48) for i in range(7)]

        with self.assertLogs('apps.lead.voice_sweep', level='ERROR'):
            result = self._sweep()
        self.assertTrue(result.stopped)
        self.assertEqual(result.deleted, 0)
        self.assertTrue(all(self._exists(k) for k in orphans))

        result = self._sweep(force=True)
        self.assertEqual(result.deleted, 7)
        self.assertFalse(any(self._exists(k) for k in orphans))

    def test_r2_bucket_is_listed_page_by_page(self):
        from datetime import timedelta
        from unittest import mock

        from django.test import override_settings

        old = timezone.now() - timedelta(days=3)
        new = timezone.now() - timedelta(hours=1)

        class FakeBucket:
            def __init__(self, objects):
                self.objects = dict(objects)
                self.deleted = []

            def get_paginator(self, name):
                bucket = self

                class Pages:
                    def paginate(self, Bucket, Prefix):
                        keys = sorted(k for k in bucket.objects if k.startswith(Prefix))
                        for i in range(0, len(keys), 2):
                            yield {'Contents': [{'Key': k, 'LastModified': bucket.objects[k]} for k in keys[i:i + 2]]}
                        if not keys:
                            yield {}

                return Pages()

            def delete_object(self, Bucket, Key):
                self.deleted.append((Bucket, Key))
                self.objects.pop(Key, None)

        bucket = FakeBucket({
            'voice-notes/1/a.webm': old, 'voice-notes/1/b.webm': old,
            'voice-notes/1/c.webm': old, 'voice-notes/2/d.webm': new,
            'uploads/x.pdf': old,
        })
        self._note('voice-notes/1/b.webm')
        r2 = override_settings(
            R2_ACCOUNT_ID='a' * 32, R2_ACCESS_KEY_ID='k', R2_SECRET_ACCESS_KEY='s', R2_BUCKET_NAME='crm-files',
        )
        with r2, mock.patch('apps.lead.voice_sweep.get_r2_client', return_value=bucket), \
                mock.patch('apps.lead.voice_notes.get_r2_client', return_value=bucket), \
                mock.patch('apps.lead.voice_sweep.PAGE', 2):
            result = self._sweep()
        self.assertEqual(result.storage, 'R2 bucket crm-files')
        self.assertEqual((result.checked, result.kept, result.too_new), (4, 1, 1))
        self.assertEqual(sorted(bucket.deleted),
                         [('crm-files', 'voice-notes/1/a.webm'), ('crm-files', 'voice-notes/1/c.webm')])
        self.assertIn('uploads/x.pdf', bucket.objects)

    def test_command_dry_run_lists_the_files(self):
        orphan = self._file('voice-notes/1/orphan.webm', 48)
        out = StringIO()
        call_command('sweep_voice_note_files', '--dry-run', stdout=out)
        text = out.getvalue()
        self.assertIn(f'no voice note: {orphan}', text)
        self.assertIn('Dry run: nothing deleted.', text)
        self.assertTrue(self._exists(orphan))

    def test_beat_runs_it_every_night(self):
        from django.conf import settings

        from skillshikshyacrm.celery import app

        entry = settings.CELERY_BEAT_SCHEDULE['sweep-voice-note-files-nightly']
        self.assertIn(entry['task'], app.tasks)
        self.assertEqual((entry['schedule'].hour, entry['schedule'].minute), ({20}, {15}))  # 02:00 NPT

    def test_nightly_job_logs_what_it_did(self):
        from apps.lead.tasks import sweep_voice_note_files_task

        self._file('voice-notes/1/orphan.webm', 48)
        with self.assertLogs('apps.lead.tasks', level='INFO') as logs:
            sweep_voice_note_files_task.apply()
        self.assertIn('1 deleted', logs.output[0])


class FakeBucket:
    """Just enough of an R2 client for the voice note tests."""

    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objects[Key] = (Body, ContentType)

    def get_object(self, Bucket, Key):
        from io import BytesIO

        return {'Body': BytesIO(self.objects[Key][0])}

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)


class VoiceNoteUploadJobTests(APITestCase):
    """A recording is saved locally at once; the worker then moves it to R2."""

    def setUp(self):
        import shutil
        import tempfile
        from unittest import mock

        from django.test import override_settings

        from apps.lead.tasks import upload_voice_note_file_task

        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        r2 = override_settings(
            MEDIA_ROOT=media, R2_ACCOUNT_ID='a' * 32, R2_ACCESS_KEY_ID='k',
            R2_SECRET_ACCESS_KEY='s', R2_BUCKET_NAME='crm-files',
        )
        r2.enable()
        self.addCleanup(r2.disable)
        self.bucket = FakeBucket()
        client = mock.patch('apps.lead.voice_notes.get_r2_client', return_value=self.bucket)
        client.start()
        self.addCleanup(client.stop)
        self.queued = []
        queue = mock.patch.object(
            upload_voice_note_file_task, 'delay', side_effect=lambda *args: self.queued.append(args),
        )
        queue.start()
        self.addCleanup(queue.stop)

        self.user = User.objects.create_user(email='rec@test.com', name='Sita', password='pass', is_staff=True)
        self.client.force_authenticate(self.user)
        self.course = Course.objects.create(name='Python', full_amount=30000)
        self.lead = Lead.objects.create(full_name='Ram Thapa', phone='9800000970')
        self.enquiry = LeadCourseInterest.objects.create(lead=self.lead, course=self.course)

    def _record(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f'/api/leads/{self.lead.id}/voice-notes/',
                {'file': SimpleUploadedFile('n.webm', b'voice-bytes', content_type='audio/webm'),
                 'enquiry': self.enquiry.id, 'duration_seconds': 5},
                format='multipart',
            )
        self.assertEqual(response.status_code, 201, response.data)
        return VoiceNote.objects.get(pk=response.data['id'])

    def _local(self, key):
        from django.core.files.storage import default_storage

        return default_storage.exists(key)

    def _play(self, note):
        return self.client.get(f'/api/leads/{self.lead.id}/voice-notes/{note.id}/audio/')

    def _run_queued(self):
        from apps.lead.tasks import upload_voice_note_file_task

        for args in self.queued:
            upload_voice_note_file_task.apply(args=args)
        self.queued.clear()

    def test_saved_locally_then_moved_to_r2_by_the_worker(self):
        note = self._record()
        self.assertEqual(self.queued, [(note.file_key, 'audio/webm')])
        self.assertTrue(self._local(note.file_key))
        self.assertEqual(self.bucket.objects, {})
        self.assertEqual(self._play(note).content, b'voice-bytes')

        self._run_queued()
        self.assertFalse(self._local(note.file_key))
        self.assertEqual(self.bucket.objects[note.file_key], (b'voice-bytes', 'audio/webm'))
        self.assertEqual(self._play(note).content, b'voice-bytes')

    def test_running_the_move_twice_is_harmless(self):
        from apps.lead.voice_notes import upload_file

        note = self._record()
        self.assertIn('moved to R2', upload_file(note.file_key, 'audio/webm'))
        self.assertIn('nothing to move', upload_file(note.file_key, 'audio/webm'))
        self.assertIn(note.file_key, self.bucket.objects)

    def test_note_deleted_before_the_move(self):
        from apps.lead.voice_notes import upload_file

        note = self._record()
        key = note.file_key
        VoiceNote.objects.filter(pk=note.pk).delete()  # no signal: the file is still there
        self.assertIn('voice note was deleted', upload_file(key, 'audio/webm'))
        self.assertFalse(self._local(key))
        self.assertEqual(self.bucket.objects, {})

    def test_r2_down_retries_and_the_file_keeps_playing(self):
        from unittest import mock

        from apps.lead.tasks import upload_voice_note_file_task

        note = self._record()
        with mock.patch.object(self.bucket, 'put_object', side_effect=ConnectionError('R2 down')):
            result = upload_voice_note_file_task.apply(args=[note.file_key, 'audio/webm'])
        self.assertTrue(result.failed())
        self.assertTrue(self._local(note.file_key))
        self.assertEqual(self._play(note).content, b'voice-bytes')

    def test_redis_down_keeps_it_local_until_the_nightly_run(self):
        from datetime import timedelta
        from unittest import mock

        from apps.lead.tasks import upload_voice_note_file_task
        from apps.lead.voice_sweep import queue_waiting_uploads

        with mock.patch.object(upload_voice_note_file_task, 'delay', side_effect=ConnectionError('no redis')), \
                self.assertLogs('apps.lead.voice_notes', level='WARNING'):
            note = self._record()
        self.assertTrue(self._local(note.file_key))
        self.assertEqual(queue_waiting_uploads(), 0)  # too new: its own job may still run
        later = timezone.now() + timedelta(hours=2)
        self.assertEqual(queue_waiting_uploads(now=later), 1)
        self._run_queued()
        self.assertIn(note.file_key, self.bucket.objects)
        self.assertEqual(queue_waiting_uploads(now=later), 0)

    def test_without_r2_it_stays_local_and_nothing_is_queued(self):
        from django.test import override_settings

        with override_settings(R2_ACCOUNT_ID='', R2_ACCESS_KEY_ID='', R2_SECRET_ACCESS_KEY='', R2_BUCKET_NAME=''):
            note = self._record()
            self.assertEqual(self.queued, [])
            self.assertTrue(self._local(note.file_key))
            self.assertEqual(self._play(note).content, b'voice-bytes')

    def test_delete_removes_it_from_both_places(self):
        from apps.lead.voice_notes import delete_file

        waiting = self._record()
        moved = self._record()
        self.queued = [q for q in self.queued if q[0] == moved.file_key]
        self._run_queued()
        for note in (waiting, moved):
            delete_file(note.file_key)
            self.assertFalse(self._local(note.file_key))
            self.assertNotIn(note.file_key, self.bucket.objects)


class EnquiryDetailsTests(APITestCase):
    """Adding an enquiry: what it is about, and a description typed or recorded."""

    def setUp(self):
        import shutil
        import tempfile
        from unittest import mock

        from django.test import override_settings

        from apps.lead.tasks import upload_voice_note_file_task

        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        local = override_settings(
            MEDIA_ROOT=media, R2_ACCOUNT_ID='', R2_ACCESS_KEY_ID='', R2_SECRET_ACCESS_KEY='', R2_BUCKET_NAME='',
        )
        local.enable()
        self.addCleanup(local.disable)
        queue = mock.patch.object(upload_voice_note_file_task, 'delay')
        self.delay = queue.start()
        self.addCleanup(queue.stop)
        self.user = User.objects.create_user(email='enq@test.com', name='Sita', password='pass', is_staff=True)
        self.client.force_authenticate(self.user)
        self.course = Course.objects.create(name='Python', full_amount=30000)
        self.lead = Lead.objects.create(full_name='Ram Thapa', phone='9800000980')
        self.url = f'/api/leads/{self.lead.id}/course-interests/'

    def _post(self, data, fmt='json', expect=201):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.url, data, format=fmt)
        self.assertEqual(response.status_code, expect, response.data)
        return response.data

    def _audio(self, content_type='audio/webm'):
        from django.core.files.uploadedfile import SimpleUploadedFile

        return SimpleUploadedFile('d.webm', b'voice-bytes', content_type=content_type)

    def test_course_only_still_works(self):
        # The lead form and the phone match box add enquiries with a course only.
        data = self._post({'course': self.course.id})
        self.assertEqual((data['about'], data['description']), ('', ''))

    def test_enquiry_without_a_course_goes_by_what_it_is_about(self):
        first = self._post({'about': 'Weekend batch', 'description': 'Asked the fee.'})
        self._post({'about': 'Scholarship', 'description': 'Asked about discounts.'})
        self.assertEqual((first['course'], first['course_name']), (None, None))
        self.assertEqual(LeadCourseInterest.objects.filter(lead=self.lead, course=None).count(), 2)

        self._post(
            {'about': 'Job help', 'recording': self._audio(), 'recording_seconds': 4}, fmt='multipart',
        )
        notes = self.client.get(f'/api/leads/{self.lead.id}/voice-notes/').data['results']
        self.assertEqual([n['course_name'] for n in notes], ['Job help'])

        timeline = self.client.get(f'/api/leads/{self.lead.id}/timeline/').data['results']
        added = {i['course'] for i in timeline if i['title'] == 'Enquiry added'}
        self.assertEqual(added, {'Weekend batch', 'Scholarship', 'Job help'})

    def test_typed_description(self):
        data = self._post({'course': self.course.id, 'about': '  Weekend batch ', 'description': ' Asked the fee. '})
        self.assertEqual((data['about'], data['description']), ('Weekend batch', 'Asked the fee.'))
        self.assertFalse(VoiceNote.objects.exists())

    def test_recorded_description_becomes_a_voice_note_on_the_enquiry(self):
        data = self._post(
            {'course': self.course.id, 'about': 'Fees', 'recording': self._audio(), 'recording_seconds': 7},
            fmt='multipart',
        )
        note = VoiceNote.objects.get()
        self.assertEqual((note.enquiry_id, note.duration_seconds, note.created_by), (data['id'], 7, self.user))
        self.assertEqual(data['description'], '')
        audio = self.client.get(f'/api/leads/{self.lead.id}/voice-notes/{note.id}/audio/')
        self.assertEqual(audio.content, b'voice-bytes')

    def test_bad_recording_saves_nothing(self):
        refused = self._post(
            {'course': self.course.id, 'about': 'Fees', 'recording': self._audio('application/pdf')},
            fmt='multipart', expect=400,
        )
        self.assertEqual(refused['recording'], ['Only a sound recording can be saved.'])
        self.assertFalse(LeadCourseInterest.objects.exists())
        self.assertFalse(VoiceNote.objects.exists())

    def test_refused_enquiry_keeps_no_recording(self):
        LeadCourseInterest.objects.create(lead=self.lead, course=self.course)
        self._post(
            {'course': self.course.id, 'about': 'Fees', 'recording': self._audio()},
            fmt='multipart', expect=400,
        )
        self.assertFalse(VoiceNote.objects.exists())


class PendingEnrollmentTests(SeatTestBase):
    """The lead list card: leads whose enrollment is pending, not yet confirmed."""

    def setUp(self):
        super().setUp()
        from apps.branch.models import Branch

        self.ktm = Branch.objects.create(name='Kathmandu', code='KTM')
        self.pkr = Branch.objects.create(name='Pokhara', code='PKR')

    def _with_enrollment(self, status_, branch=None):
        lead = self._lead()
        lead.branch = branch
        lead.save()
        Enrollment.objects.create(
            lead=lead, course=self.course, enrollment_date=date.today(), status=status_,
            listed_fee=30000, agreed_amount=30000, branch=branch,
        )
        return lead

    def test_card_count_and_list(self):
        pending = self._with_enrollment('pending', self.ktm)
        pending_pkr = self._with_enrollment('pending', self.pkr)
        self._with_enrollment('confirmed', self.ktm)
        binned = self._with_enrollment('pending', self.ktm)
        binned.is_deleted = True
        binned.save()
        self._lead()

        self.assertEqual(self.client.get('/api/leads/summary/').data['pending_enrollment'], 2)
        listed = self.client.get('/api/leads/', {'enrollment_status': 'pending'}).data['results']
        self.assertEqual(sorted(r['id'] for r in listed), sorted([pending.id, pending_pkr.id]))
        self.assertEqual(self.client.get('/api/leads/', {'enrollment_status': 'nope'}).status_code, 400)

    def test_branch_counts_its_own(self):
        pending = self._with_enrollment('pending', self.ktm)
        self._with_enrollment('pending', self.pkr)
        clerk = User.objects.create_user(email='ktm@test.com', name='Ktm', password='pass', branch=self.ktm)
        give_view_rights(clerk)
        self.client.force_authenticate(clerk)
        self.assertEqual(self.client.get('/api/leads/summary/').data['pending_enrollment'], 1)
        listed = self.client.get('/api/leads/', {'enrollment_status': 'pending'}).data['results']
        self.assertEqual([r['id'] for r in listed], [pending.id])

class LeadTimelineTests(SeatTestBase):
    """One list with everything about the person, newest first."""

    def test_everything_shows_newest_first_and_can_be_narrowed(self):
        from apps.branch.models import Branch
        from apps.lead.enquiries import add_enquiry
        from apps.lead.models import LeadCall

        lead = self._lead()
        enquiry = add_enquiry(lead, course=self.course, user=self.admin)
        LeadCall.objects.create(
            lead=lead, call_timestamp=timezone.now(), caller='Admin', phone_number=lead.phone,
            duration_seconds=120, outcome='Interested', created_by=self.admin,
        )
        VoiceNote.objects.create(
            lead=lead, enquiry=enquiry, file_key='voice-notes/x.webm', content_type='audio/webm',
            size_bytes=1, duration_seconds=30, note='Wants weekends', created_by=self.admin,
        )
        enrollment = self._enroll(self.batch, lead=lead)
        self.assertEqual(self._confirm(enrollment).status_code, 200)
        pause = self.client.post(self._url(enrollment, 'pause/'), {'reason': 'Exams'}, format='json')
        self.assertEqual(pause.status_code, 200, pause.data)
        Payment.objects.create(
            enrollment=enrollment, amount=Decimal('5000'), payment_date=timezone.now(),
            payment_method='cash', received_by=self.admin,
        )

        response = self.client.get(f'/api/leads/{lead.id}/timeline/')
        self.assertEqual(response.status_code, 200)
        items = response.data['results']
        self.assertEqual(response.data['count'], len(items))
        times = [i['at'] for i in items]
        self.assertEqual(times, sorted(times, reverse=True))
        self.assertEqual(
            {i['kind'] for i in items},
            {'lead', 'enquiry', 'call', 'voice_note', 'enrollment', 'batch', 'money'},
        )
        titles = {i['title'] for i in items}
        for title in ['Lead added', 'Enquiry added', 'Enquiry won', 'Outbound call', 'Voice note',
                      'Enrollment added', 'Confirm', 'Pause', 'Payment received']:
            self.assertIn(title, titles)
        voice = next(i for i in items if i['kind'] == 'voice_note')
        self.assertEqual(
            (voice['voice_note'], voice['course'], voice['detail'], voice['duration_seconds']),
            (VoiceNote.objects.get().id, 'Python', 'Wants weekends', 30),
        )
        paused = next(i for i in items if i['title'] == 'Pause')
        self.assertEqual((paused['detail'], paused['enrollment']), ('Exams', enrollment.id))

        only_batch = self.client.get(f'/api/leads/{lead.id}/timeline/', {'kind': 'batch'}).data
        self.assertEqual({i['kind'] for i in only_batch['results']}, {'batch'})

        outsider = User.objects.create_user(
            email='far@test.com', name='Far', password='pass',
            branch=Branch.objects.create(name='Pokhara', code='PKR'),
        )
        give_view_rights(outsider)
        self.client.force_authenticate(outsider)
        self.assertEqual(self.client.get(f'/api/leads/{lead.id}/timeline/').status_code, 404)


class LeadChangeTests(APITestCase):
    """Each status or counselor change on a lead leaves a row, shown in the lead's history."""

    def setUp(self):
        from apps.lead.models import LeadStatus

        self.admin = User.objects.create_user(
            email='changes@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.new = LeadStatus.objects.create(name='New', sort_order=1)
        self.contacted = LeadStatus.objects.create(name='Contacted', sort_order=2)
        self.sita = User.objects.create_user(email='sita@test.com', name='Sita', password='pass')
        self.hari = User.objects.create_user(email='hari@test.com', name='Hari', password='pass')
        self.lead = Lead.objects.create(
            full_name='Ram Thapa', phone='9800000950', status=self.new, assigned_counselor=self.sita,
        )

    def _patch(self, **body):
        response = self.client.patch(f'/api/leads/{self.lead.id}/', body, format='json')
        self.assertEqual(response.status_code, 200, response.data)

    def _rows(self):
        from apps.lead.models import LeadChange

        return list(
            LeadChange.objects.filter(lead=self.lead).order_by('id')
            .values_list('field', 'from_value', 'to_value', 'changed_by__name')
        )

    def test_status_and_counselor_changes_are_recorded_with_who(self):
        self._patch(status=self.contacted.id)
        self._patch(assigned_counselor=self.hari.id)
        self.assertEqual(self._rows(), [
            ('status', 'New', 'Contacted', 'Admin'),
            ('counselor', 'Sita', 'Hari', 'Admin'),
        ])

    def test_no_row_when_nothing_tracked_changes(self):
        self._patch(status=self.new.id, notes='Called back')
        self.lead.refresh_from_db()
        self.lead.save(update_fields=['lead_score'])
        self.assertEqual(self._rows(), [])

    def test_a_change_from_the_shell_has_no_user_and_a_reload_is_not_a_change(self):
        Lead.objects.filter(pk=self.lead.pk).update(status=self.contacted)
        self.lead.refresh_from_db()
        self.lead.save()
        self.assertEqual(self._rows(), [])
        self.lead.assigned_counselor = None
        self.lead.save()
        self.assertEqual(self._rows(), [('counselor', 'Sita', '', None)])

    def test_rows_cannot_be_changed_and_show_in_the_history(self):
        from apps.lead.models import LeadChange

        self._patch(status=self.contacted.id)
        row = LeadChange.objects.get()
        with self.assertRaises(ValidationError):
            row.save()
        with self.assertRaises(ValidationError):
            row.delete()
        items = self.client.get(f'/api/leads/{self.lead.id}/timeline/', {'kind': 'lead'}).data['results']
        changed = next(i for i in items if i['title'] == 'Status changed')
        self.assertEqual(
            (changed['before'], changed['after'], changed['by']),
            ({'Status': 'New'}, {'Status': 'Contacted'}, 'Admin'),
        )


class FollowUpLeadDatesTests(APITestCase):
    """Follow-ups keep the lead's next follow-up and last contacted dates current."""

    def setUp(self):
        self.admin = User.objects.create_user(
            email='fu-dates@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.counselor = User.objects.create_user(
            email='fu-counselor@test.com', name='Counselor A', password='pass',
        )
        self.client.force_authenticate(self.admin)
        self.lead = Lead.objects.create(full_name='Follow Up', phone='9800009001')
        self.url = f'/api/leads/{self.lead.id}/followups/'
        self.tomorrow = (timezone.now() + timedelta(days=1)).replace(microsecond=0)

    def _schedule(self, when, **extra):
        body = {'scheduled_at': when.isoformat(), 'assigned_to': self.counselor.id, **extra}
        response = self.client.post(self.url, body, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        return response.data['id']

    def _patch(self, followup_id, body):
        response = self.client.patch(f'{self.url}{followup_id}/', body, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response.data

    def _lead(self):
        return self.client.get(f'/api/leads/{self.lead.id}/').data

    def test_completing_the_follow_up_clears_next_and_sets_last_contacted(self):
        followup = self._schedule(self.tomorrow)
        lead = self._lead()
        self.assertEqual(lead['next_followup']['id'], followup)
        self.assertIsNotNone(lead['next_followup_at'])

        # What the Follow-ups page sends.
        done_at = timezone.now().replace(microsecond=0)
        self._patch(followup, {'status': 'completed', 'completed_at': done_at.isoformat()})

        lead = self._lead()
        self.assertIsNone(lead['next_followup'])
        self.assertIsNone(lead['next_followup_at'])
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.last_contact_at, done_at)

    def test_next_moves_to_the_next_open_follow_up(self):
        later = self.tomorrow + timedelta(days=6)
        first = self._schedule(self.tomorrow)
        self._schedule(later)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.next_followup_at, self.tomorrow)

        self._patch(first, {'status': 'completed'})
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.next_followup_at, later)
        self.assertIsNotNone(self.lead.last_contact_at)

    def test_completing_without_a_time_uses_now(self):
        followup = self._schedule(self.tomorrow)
        before = timezone.now()
        data = self._patch(followup, {'status': 'completed'})
        self.assertIsNotNone(data['completed_at'])
        self.lead.refresh_from_db()
        self.assertGreaterEqual(self.lead.last_contact_at, before)

    def test_cancelling_or_deleting_clears_next_without_counting_as_contact(self):
        cancelled = self._schedule(self.tomorrow)
        deleted = self._schedule(self.tomorrow + timedelta(days=2))
        self._patch(cancelled, {'status': 'cancelled'})
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.next_followup_at, self.tomorrow + timedelta(days=2))

        response = self.client.delete(f'{self.url}{deleted}/')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.lead.refresh_from_db()
        self.assertIsNone(self.lead.next_followup_at)
        self.assertIsNone(self.lead.last_contact_at)

    def test_last_contacted_never_moves_back(self):
        call_time = timezone.now().replace(microsecond=0)
        Lead.objects.filter(pk=self.lead.pk).update(last_contact_at=call_time)
        followup = self._schedule(self.tomorrow)
        self._patch(followup, {
            'status': 'completed',
            'completed_at': (call_time - timedelta(days=3)).isoformat(),
        })
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.last_contact_at, call_time)

    def test_old_stale_dates_are_fixed_once(self):
        from importlib import import_module

        from django.apps import apps

        followup = self._schedule(self.tomorrow)
        done_at = timezone.now().replace(microsecond=0)
        FollowUp.objects.filter(pk=followup).update(status='completed', completed_at=done_at)
        Lead.objects.filter(pk=self.lead.pk).update(next_followup_at=self.tomorrow, last_contact_at=None)

        import_module('apps.lead.migrations.0031_sync_followup_dates').sync(apps, None)
        self.lead.refresh_from_db()
        self.assertEqual((self.lead.next_followup_at, self.lead.last_contact_at), (None, done_at))


class BranchHistoryTests(APITestCase):
    """A person who enquired at two branches: each branch sees only its own records in History."""

    def setUp(self):
        from apps.branch.models import Branch
        from apps.lead.enquiries import add_enquiry
        from apps.lead.models import LeadCall

        self.ktm = Branch.objects.create(name='Kathmandu', code='KTM')
        self.pkr = Branch.objects.create(name='Pokhara', code='PKR')
        self.admin = User.objects.create_user(
            email='bh-admin@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.ktm_user = User.objects.create_user(
            email='bh-ktm@test.com', name='Ktm Counselor', password='pass', branch=self.ktm,
        )
        self.pkr_user = User.objects.create_user(
            email='bh-pkr@test.com', name='Pkr Counselor', password='pass', branch=self.pkr,
        )
        give_view_rights(self.ktm_user, self.pkr_user)
        python = Course.objects.create(name='Python', full_amount=30000)
        react = Course.objects.create(name='React', full_amount=20000)
        self.lead = Lead.objects.create(full_name='Two Branches', phone='9800009101', branch=self.ktm)

        # Kathmandu: an enquiry, a call, a voice note, a student with money.
        ktm_enquiry = add_enquiry(self.lead, course=python, user=self.ktm_user)
        LeadCall.objects.create(
            lead=self.lead, call_timestamp=timezone.now(), caller='Ktm', phone_number='9800009101',
            branch=self.ktm, created_by=self.ktm_user,
        )
        self.ktm_note = VoiceNote.objects.create(
            lead=self.lead, enquiry=ktm_enquiry, file_key='voice-notes/ktm.webm',
            content_type='audio/webm', size_bytes=1, branch=self.ktm, created_by=self.ktm_user,
        )
        enrollment = Enrollment.objects.create(
            lead=self.lead, course=python, enrollment_date=date.today(), listed_fee=30000,
            agreed_amount=30000, status='confirmed', branch=self.ktm,
        )
        Payment.objects.create(
            enrollment=enrollment, amount=Decimal('5000'), payment_date=timezone.now(),
            payment_method='cash', received_by=self.ktm_user, branch=self.ktm,
        )

        # Pokhara: the same person asks about React there.
        pkr_enquiry = add_enquiry(self.lead, course=react, user=self.pkr_user)
        LeadCall.objects.create(
            lead=self.lead, call_timestamp=timezone.now(), caller='Pkr', phone_number='9800009101',
            branch=self.pkr, created_by=self.pkr_user,
        )
        self.pkr_note = VoiceNote.objects.create(
            lead=self.lead, enquiry=pkr_enquiry, file_key='voice-notes/pkr.webm',
            content_type='audio/webm', size_bytes=1, branch=self.pkr, created_by=self.pkr_user,
        )

    def _history(self, user):
        self.client.force_authenticate(user)
        response = self.client.get(f'/api/leads/{self.lead.id}/timeline/')
        self.assertEqual(response.status_code, 200)
        return response.data['results']

    def test_the_other_branch_sees_none_of_the_students_or_money(self):
        items = self._history(self.pkr_user)
        kinds = {i['kind'] for i in items}
        self.assertFalse(kinds & {'enrollment', 'batch', 'money', 'teams', 'reminder'})
        self.assertEqual(
            sorted((i['title'], i['course'] or i['by']) for i in items if i['kind'] != 'lead'),
            sorted([('Enquiry added', 'React'), ('Outbound call', 'Pkr Counselor'),
                    ('Voice note', 'React')]),
        )
        self.assertIn('Lead added', {i['title'] for i in items})
        summary = self.client.get(f'/api/leads/{self.lead.id}/').data['enrollment_summary']
        self.assertFalse(summary['is_enrolled'])
        give_view_rights(self.ktm_user)
        self.client.force_authenticate(self.ktm_user)
        summary = self.client.get(f'/api/leads/{self.lead.id}/').data['enrollment_summary']
        self.assertEqual((summary['is_enrolled'], summary['course']), (True, 'Python'))

    def test_each_branch_sees_its_own_and_staff_see_both(self):
        ktm = self._history(self.ktm_user)
        self.assertIn('Payment received', {i['title'] for i in ktm})
        self.assertNotIn('React', {i['course'] for i in ktm})
        self.assertEqual({i['voice_note'] for i in ktm if i['voice_note']}, {self.ktm_note.id})

        everything = self._history(self.admin)
        self.assertEqual(
            {i['voice_note'] for i in everything if i['voice_note']},
            {self.ktm_note.id, self.pkr_note.id},
        )
        self.assertEqual({i['course'] for i in everything if i['kind'] == 'enquiry'}, {'Python', 'React'})

    def test_the_other_branch_cannot_list_or_play_its_voice_notes(self):
        give_view_rights(self.pkr_user)
        self.client.force_authenticate(self.pkr_user)
        url = f'/api/leads/{self.lead.id}/voice-notes/'
        listed = self.client.get(url).data['results']
        self.assertEqual([n['id'] for n in listed], [self.pkr_note.id])
        self.assertEqual(self.client.get(f'{url}{self.ktm_note.id}/audio/').status_code, 404)


class EmailTests(SeatTestBase):
    """Real emails: who gets each one, what goes with it, and the log on the lead."""

    def setUp(self):
        from django.test import override_settings

        super().setUp()
        # No logo, so making a PDF never goes to the network.
        no_logo = override_settings(INVOICE_LOGO_URL='')
        no_logo.enable()
        self.addCleanup(no_logo.disable)
        self.counselor = User.objects.create_user(email='counselor@test.com', name='Gita', password='x')

    def _logs(self, kind):
        from apps.global_config.models import EmailLog

        return list(EmailLog.objects.filter(kind=kind).order_by('id'))

    def _send(self, log):
        from apps.global_config.tasks import send_email_task

        send_email_task.apply(args=(log.id,))
        log.refresh_from_db()
        return log

    def _student_lead(self):
        lead = self._lead()
        lead.email = 'ram@student.com'
        lead.save(update_fields=['email'])
        return lead

    def test_new_lead_assigned_emails_the_counselor_not_on_self_assign(self):
        from django.core import mail

        response = self.client.post('/api/leads/', {
            'full_name': 'Hari', 'phone': '9800077001', 'assigned_counselor': self.counselor.id,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        [log] = self._logs('lead_assigned')
        self.assertEqual((log.to_email, log.lead_id, log.created_by), ('counselor@test.com', response.data['id'], self.admin))
        self.assertIn('Hari', log.body_html)
        self.assertIn(f"/leads/{response.data['id']}", log.body_text)

        self._send(log)
        self.assertEqual(log.status, 'sent')
        self.assertEqual(mail.outbox[0].to, ['counselor@test.com'])
        self.assertEqual(mail.outbox[0].subject, 'New lead assigned: Hari')

        self.client.force_authenticate(self.counselor)
        self.counselor.is_staff = True
        self.counselor.save()
        self.client.post('/api/leads/', {
            'full_name': 'Own lead', 'phone': '9800077002', 'assigned_counselor': self.counselor.id,
        }, format='json')
        self.assertEqual(len(self._logs('lead_assigned')), 1)

    def test_pending_email_is_dropped_when_confirmed_first(self):
        lead = self._student_lead()
        enrollment = self._enroll(self.batch, lead=lead)
        [pending] = self._logs('enrollment_pending')
        self.assertEqual(pending.to_email, 'ram@student.com')
        self.assertIn('Python Nov', pending.body_html)

        self._confirm(enrollment)
        self.assertEqual(self._send(pending).status, 'skipped')
        [confirmed] = self._logs('enrollment_confirmed')
        self.assertEqual(confirmed.invoice.type, 'enrollment')

    def test_confirmed_email_carries_the_invoice_pdf(self):
        from django.core import mail

        lead = self._student_lead()
        enrollment = self._enroll(self.batch, lead=lead)
        self._confirm(enrollment)
        [log] = self._logs('enrollment_confirmed')
        self._send(log)
        self.assertEqual(log.status, 'sent')
        message = mail.outbox[-1]
        [(name, content, kind)] = message.attachments
        self.assertEqual((name, kind), (f'{log.invoice.invoice_number}.pdf', 'application/pdf'))
        self.assertTrue(content.startswith(b'%PDF'))
        log.invoice.refresh_from_db()
        self.assertEqual(log.invoice.emailed_to, 'ram@student.com')
        self.assertIsNotNone(log.invoice.sent_at)
        # No separate invoice email on confirm.
        self.assertEqual(self._logs('invoice'), [])

    def test_payment_email_carries_the_receipt_and_copies_accounts(self):
        from django.core import mail

        from apps.invoices.models import InvoiceEmailConfig

        InvoiceEmailConfig.objects.create(pk=1, cc_emails=['accounts@test.com'], is_active=True)
        lead = self._student_lead()
        enrollment = self._enroll(self.batch, lead=lead)
        # The first payment is taken with Confirm: the receipt email goes with the confirmation.
        response = self.client.post(self._url(enrollment, 'confirm/'), {'first_payment': {
            'amount': '10000', 'payment_method': 'esewa', 'transaction_reference': 'ES-1',
        }}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(self._logs('enrollment_confirmed')), 1)
        [log] = self._logs('payment_received')
        self.assertEqual((log.to_email, log.cc, log.invoice.type), ('ram@student.com', ['accounts@test.com'], 'payment'))
        self.assertIn('NPR 10,000', log.subject)
        self.assertIn('NPR 20,000', log.body_text)  # balance
        self._send(log)
        self.assertEqual(mail.outbox[-1].cc, ['accounts@test.com'])

    def test_no_address_keeps_a_not_sent_row_off_the_history(self):
        enrollment = self._enroll(self.batch)
        [log] = self._logs('enrollment_pending')
        self.assertEqual((log.status, log.error), ('skipped', 'No email address.'))
        items = self.client.get(f'/api/leads/{enrollment.lead_id}/timeline/').data['results']
        self.assertFalse([i for i in items if 'mail' in i['title'].lower()])

    def test_a_refused_send_is_kept_with_the_reason(self):
        import smtplib
        from unittest import mock

        from apps.global_config.mailer import send_now

        lead = self._student_lead()
        with mock.patch('django.core.mail.EmailMultiAlternatives.send',
                        side_effect=smtplib.SMTPRecipientsRefused({})):
            log = send_now(
                'teams_account', to='ram@student.com', subject='Sign in', lead=lead,
                template='lead/emails/invoice.html',
                context={'label': 'Invoice', 'invoice': {'invoice_number': 'X'}, 'name': 'Ram', 'rows': []},
                hidden_context={},
            )
        self.assertEqual((log.status, log.error), ('failed', 'The server refused the To address.'))

    def test_invoice_pdf_endpoint(self):
        enrollment = self._enroll(self.batch)
        self._confirm(enrollment)
        invoice = Invoice.objects.get(enrollment=enrollment, type='enrollment')
        response = self.client.get(f'/api/invoices/{invoice.id}/pdf/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF'))

    def test_organisation_details_are_used(self):
        saved = self.client.put('/api/settings/organisation/', {
            'name': 'Skill Shikshya Pvt. Ltd.', 'address': 'Sankhamul, Kathmandu',
            'phone': '01-5199999', 'pan_number': '609876543',
        }, format='json')
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertEqual(saved.data['pan_number'], '609876543')

        from apps.invoices.pdf import document_html

        enrollment = self._enroll(self.batch)
        self._confirm(enrollment)
        html = document_html(Invoice.objects.get(enrollment=enrollment, type='enrollment'))
        self.assertIn('PAN/VAT No: 609876543', html)
        self.assertIn('Rupees Thirty Thousand Only', html)

        self.client.force_authenticate(self.counselor)
        self.assertEqual(self.client.get('/api/settings/organisation/').status_code, 403)


class AmountInWordsTests(APITestCase):
    def test_nepali_grouping_and_paisa(self):
        from apps.invoices.pdf import amount_in_words

        self.assertEqual(amount_in_words(125000), 'Rupees One Lakh Twenty-Five Thousand Only')
        self.assertEqual(amount_in_words(Decimal('20000.50')), 'Rupees Twenty Thousand and Fifty Paisa Only')
        self.assertEqual(amount_in_words(12500000), 'Rupees One Crore Twenty-Five Lakh Only')
        self.assertEqual(amount_in_words(999), 'Rupees Nine Hundred Ninety-Nine Only')


class LeadImportExportTests(APITestCase):
    """Import checks every row before saving; export gives back what the list shows."""

    def setUp(self):
        from apps.branch.models import Branch
        from apps.lead.models import LeadStatus, LeadTag

        self.ktm = Branch.objects.create(name='Kathmandu', code='KTM')
        self.pkr = Branch.objects.create(name='Pokhara', code='PKR')
        self.admin = User.objects.create_user(
            email='import@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.counselor = User.objects.create_user(
            email='sita@test.com', name='Sita Rai', password='pass', branch=self.ktm,
        )
        self.facebook = LeadSource.objects.create(name='Facebook')
        self.new = LeadStatus.objects.create(name='New')
        self.hot = LeadTag.objects.create(name='Hot')
        self.python = Course.objects.create(name='Python', full_amount=30000)
        self.existing = Lead.objects.create(
            full_name='Old Lead', phone='+977 980-111-2222', branch=self.ktm,
        )

    # ---------------------------------------------------------- helpers

    def _csv(self, text, name='leads.csv'):
        from django.core.files.uploadedfile import SimpleUploadedFile

        return SimpleUploadedFile(name, text.encode('utf-8'), content_type='text/csv')

    def _xlsx(self, rows, name='leads.xlsx'):
        from io import BytesIO

        from django.core.files.uploadedfile import SimpleUploadedFile
        from openpyxl import Workbook

        book = Workbook()
        for row in rows:
            book.active.append(row)
        buffer = BytesIO()
        book.save(buffer)
        return SimpleUploadedFile(name, buffer.getvalue())

    def _post(self, url, upload, **fields):
        return self.client.post(url, {'file': upload, **fields}, format='multipart')

    def _preview(self, upload, **fields):
        return self._post('/api/leads/import/preview/', upload, **fields)

    def _import(self, upload, **fields):
        return self._post('/api/leads/import/', upload, **fields)

    def _rows(self, response):
        return {r['row']: r for r in response.data['rows']}

    # ---------------------------------------------------------- preview

    def test_preview_matches_columns_and_checks_each_row(self):
        upload = self._csv(
            'Name,Mobile,E-mail,Lead Type,Lead Source,Interested Course,Favourite colour\n'
            'Ram Thapa,9801234567,ram@test.com,walk-in,FACEBOOK,python,blue\n'
            ',9801234568,,,,\n'
            'Hari,98012,hari@,Walk-in,Tiktok,,\n'
            'Gita,9801112222,,,,\n'
            'Ram Again,+977-9801234567,,,,\n'
            'Excel Lost,9.80123E+09,,,,\n'
        )
        response = self._preview(upload)
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data
        self.assertEqual([c['column'] for c in data['columns']], [
            'Full name', 'Phone', 'Email', 'Source', 'Course',
        ])
        # Lead type is gone; an old file that still has the column imports without it.
        self.assertEqual(data['ignored'], ['Lead Type', 'Favourite colour'])
        self.assertEqual((data['total'], data['ready'], data['duplicates'], data['errors']), (6, 1, 2, 3))

        rows = self._rows(response)
        self.assertEqual(rows[2]['status'], 'ready')
        self.assertEqual(rows[2]['course'], 'Python')
        self.assertEqual(rows[3]['errors'], ['Full name is missing.'])
        self.assertEqual(rows[4]['errors'], [
            'Phone “98012” is not a phone number.',
            'Email “hari@” is not a valid email address.',
            'Source “Tiktok” is not in the CRM.',
        ])
        # Same number as a lead already in the CRM, typed another way.
        self.assertEqual(rows[5]['status'], 'duplicate')
        self.assertEqual(rows[5]['matches'][0]['reference_number'], self.existing.reference_number)
        # Same number as an earlier row of the file.
        self.assertEqual(rows[6]['status'], 'duplicate')
        self.assertEqual(rows[6]['same_as_row'], 2)
        self.assertIn('lost its digits in Excel', rows[7]['errors'][0])
        self.assertEqual(Lead.objects.count(), 1, 'A preview saves nothing.')

    def test_defaults_fill_blanks(self):
        upload = 'Full name,Phone\nAsha,9802000001\n'
        self.assertEqual(self._rows(self._preview(self._csv(upload)))[2]['status'], 'ready')
        response = self._preview(
            self._csv(upload), default_course=self.python.pk, default_counselor=self.counselor.pk,
        )
        row = self._rows(response)[2]
        self.assertEqual(row['status'], 'ready')
        self.assertEqual((row['course'], row['counselor'], row['branch']), ('Python', 'Sita Rai', 'Kathmandu'))

    def test_file_problems_are_refused(self):
        response = self._preview(self._csv('a,b\n1,2\n', name='leads.xls'))
        self.assertEqual(response.status_code, 400)
        self.assertIn('.xls', response.data['file'][0])
        response = self._preview(self._csv('Full name,Email\nAsha,a@test.com\n'))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data['file'][0],
            'No Phone column was found. Download the template to see the column names.',
        )
        response = self._preview(self._csv('Full name,Phone\n'))
        self.assertEqual(response.data['file'][0], 'The file has column names but no leads.')

    def test_counselor_must_be_in_the_lead_branch(self):
        upload = self._csv(
            'Full name,Phone,Branch,Counselor\n'
            'Asha,9802000001,Pokhara,sita@test.com\n'
            'Bina,9802000002,,Nobody Here\n'
        )
        rows = self._rows(self._preview(upload))
        self.assertEqual(rows[2]['errors'], ['Sita Rai is not in Pokhara.'])
        self.assertEqual(rows[3]['errors'], ['Counselor “Nobody Here” is not an active user.'])

    # ----------------------------------------------------------- import

    def test_import_saves_ready_rows_and_skips_duplicates(self):
        upload = (
            'Full name,Phone,Email,Course,Tags,Expected revenue,Notes\n'
            'Ram Thapa,9801234567,RAM@test.com,Python,"Hot, Cold","NPR 30,000",Call after 5\n'
            'Old Again,9801112222,,,,,\n'
            'No Phone,,,,,,\n'
        )
        response = self._import(self._csv(upload))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data['created'], response.data['skipped'], response.data['failed']), (1, 1, 1))
        rows = self._rows(response)
        self.assertEqual(rows[3]['message'], f'Phone is already on {self.existing.reference_number} (Old Lead).')
        self.assertEqual(rows[4]['message'], 'Phone is missing.')

        lead = Lead.objects.get(pk=rows[2]['lead_id'])
        self.assertEqual(lead.reference_number, rows[2]['reference_number'])
        self.assertEqual((lead.email, lead.primary_course), ('ram@test.com', self.python))
        self.assertEqual(lead.expected_revenue, Decimal('30000.00'))
        self.assertEqual(lead.notes, 'Call after 5')
        self.assertEqual(list(lead.tags.all()), [self.hot])
        self.assertEqual(lead.created_by, self.admin)
        self.assertEqual(lead.custom_data, {'import_file': 'leads.csv'})
        # The course becomes the lead's primary enquiry, as with the Add lead form.
        self.assertTrue(lead.course_interests.filter(course=self.python, is_primary=True).exists())

        response = self._import(self._csv(upload), duplicates='import')
        self.assertEqual(response.data['created'], 2)

    def test_excel_file_with_numeric_phones(self):
        upload = self._xlsx([
            ['Full Name', 'Phone'],
            ['Asha', 9802000001],
            [None, None],
            ['Bina', 9802000002.0],
        ])
        response = self._import(upload)
        self.assertEqual(response.data['created'], 2, response.data)
        self.assertEqual(
            sorted(Lead.objects.filter(full_name__in=['Asha', 'Bina']).values_list('phone', flat=True)),
            ['9802000001', '9802000002'],
        )

    def test_counselor_hears_once_for_many_leads(self):
        from apps.global_config.models import EmailLog
        from apps.lead.models import Notification

        upload = self._csv(
            'Full name,Phone,Counselor\n'
            'Asha,9802000001,Sita Rai\n'
            'Bina,9802000002,Sita Rai\n'
            'Chandra,9802000003,sita@test.com\n'
        )
        response = self._import(upload)
        self.assertEqual(response.data['created'], 3)
        logs = EmailLog.objects.filter(kind=EmailLog.Kind.LEAD_ASSIGNED)
        self.assertEqual([log.subject for log in logs], ['3 new leads assigned to you'])
        self.assertEqual(logs[0].to_email, 'sita@test.com')
        self.assertIn('Chandra', logs[0].body_html)
        notes = Notification.objects.filter(recipient=self.counselor)
        self.assertEqual([n.message for n in notes], ['3 imported leads have been assigned to you.'])
        # The leads went to the counselor's branch.
        self.assertEqual(set(Lead.objects.filter(assigned_counselor=self.counselor).values_list('branch', flat=True)), {self.ktm.pk})

    def test_one_lead_gets_the_usual_email(self):
        from apps.global_config.models import EmailLog

        self._import(self._csv('Full name,Phone,Counselor\nAsha,9802000001,Sita Rai\n'))
        logs = EmailLog.objects.filter(kind=EmailLog.Kind.LEAD_ASSIGNED)
        self.assertEqual([log.subject for log in logs], ['New lead assigned: Asha'])

    def test_branch_user_imports_into_their_branch_only(self):
        from django.contrib.auth.models import Permission

        user = User.objects.create_user(email='ktm@test.com', name='KTM', password='pass', branch=self.ktm)
        self.client.force_authenticate(user)
        upload = 'Full name,Phone,Branch\nAsha,9802000001,\nBina,9802000002,Pokhara\n'
        self.assertEqual(self._preview(self._csv(upload)).status_code, 403)

        user.user_permissions.add(Permission.objects.get(codename='add_lead'))
        user = User.objects.get(pk=user.pk)
        self.client.force_authenticate(user)
        response = self._import(self._csv(upload))
        self.assertEqual(response.data['created'], 1, response.data)
        rows = self._rows(response)
        self.assertEqual(rows[3]['message'], 'You can add leads only to Kathmandu.')
        self.assertEqual(Lead.objects.get(pk=rows[2]['lead_id']).branch, self.ktm)

        response = self._preview(self._csv(upload), default_branch=self.pkr.pk)
        self.assertEqual(response.status_code, 400)

    def test_editing_keeps_the_import_file(self):
        self._import(self._csv('Full name,Phone\nAsha,9802000001\n'))
        lead = Lead.objects.get(full_name='Asha')
        response = self.client.patch(
            f'/api/leads/{lead.pk}/', {'custom_data': {'has_laptop': True}}, format='json',
        )
        self.assertEqual(response.status_code, 200)
        lead.refresh_from_db()
        self.assertEqual(lead.custom_data, {'has_laptop': True, 'import_file': 'leads.csv'})

    # --------------------------------------------------------- template

    def test_templates(self):
        from io import BytesIO

        from openpyxl import load_workbook

        response = self.client.get('/api/leads/import-template/')
        self.assertEqual(response.status_code, 200)
        text = response.content.decode('utf-8-sig')
        self.assertTrue(text.startswith('Full name,Phone,Second phone,Email,Source'))

        response = self.client.get('/api/leads/import-template/', {'file_type': 'xlsx'})
        self.assertEqual(response.status_code, 200)
        book = load_workbook(BytesIO(response.content))
        self.assertEqual(book.sheetnames, ['Leads', 'Lists'])
        self.assertEqual(book['Leads']['A1'].value, 'Full name')
        lists = {book['Lists'].cell(row=1, column=c).value: c for c in range(1, 10)}
        self.assertNotIn('Lead type', lists)
        sources = [book['Lists'].cell(row=r, column=lists['Source']).value for r in range(2, 20)]
        self.assertIn('Facebook', sources)
        self.assertTrue(book['Leads'].data_validations.dataValidation)

    # ----------------------------------------------------------- export

    def test_export_csv_has_every_column_and_reads_back(self):
        lead = Lead.objects.create(
            full_name='राम थापा', phone='9801234567', email='ram@test.com',
            source=self.facebook, status=self.new, primary_course=self.python, branch=self.ktm,
            assigned_counselor=self.counselor, notes='Hello', expected_revenue=Decimal('30000'),
        )
        lead.tags.add(self.hot)
        response = self.client.get('/api/leads/export_csv/', {'ordering': 'id'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Disposition'].startswith('attachment; filename="leads-'))
        self.assertTrue(response.content.startswith('﻿'.encode('utf-8')), 'Excel needs the BOM')
        import csv as _csv
        rows = list(_csv.DictReader(StringIO(response.content.decode('utf-8-sig'))))
        self.assertEqual(len(rows), 2)
        row = rows[1]
        self.assertEqual(row['Full name'], 'राम थापा')
        self.assertNotIn('Lead type', row)
        self.assertEqual((row['Course'], row['Branch'], row['Counselor']), ('Python', 'Kathmandu', 'Sita Rai'))
        self.assertEqual((row['Tags'], row['Status'], row['Notes']), ('Hot', 'New', 'Hello'))

        # The same file reads back: the names match and the phone makes it a duplicate.
        response = self._preview(
            self._csv(response.content.decode('utf-8-sig'), name='leads-back.csv'),
        )
        self.assertEqual(response.data['duplicates'], 2, response.data)
        self.assertEqual(self._rows(response)[3]['counselor'], 'Sita Rai')
        self.assertIn('Reference', response.data['ignored'])

    def test_export_follows_filters_ids_and_branch(self):
        other = Lead.objects.create(full_name='Pokhara Lead', phone='9805550000', branch=self.pkr)
        archived = Lead.objects.create(full_name='Archived', phone='9805550001', is_archived=True)

        def names(**params):
            import csv as _csv
            response = self.client.get('/api/leads/export_csv/', params)
            return sorted(r['Full name'] for r in _csv.DictReader(StringIO(response.content.decode('utf-8-sig'))))

        self.assertEqual(names(), ['Old Lead', 'Pokhara Lead'])
        self.assertEqual(names(include_archived='true'), ['Archived', 'Old Lead', 'Pokhara Lead'])
        self.assertEqual(names(ids=f'{other.pk},{archived.pk}'), ['Pokhara Lead'])
        self.assertEqual(names(search='Pokhara'), ['Pokhara Lead'])

        give_view_rights(self.counselor)
        self.client.force_authenticate(self.counselor)
        self.assertEqual(names(), ['Old Lead'])

    def test_export_excel(self):
        from io import BytesIO

        from openpyxl import load_workbook

        response = self.client.get('/api/leads/export_csv/', {'file_type': 'xlsx'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        sheet = load_workbook(BytesIO(response.content)).active
        self.assertEqual(sheet['A1'].value, 'Reference')
        self.assertEqual(sheet['B2'].value, 'Old Lead')
        self.assertEqual(sheet['C2'].value, '+977 980-111-2222')


class IntakeGuardTests(APITestCase):
    """The public intake needs its token, makes no new courses, and is rate limited."""

    URL = '/api/public/lead-intake/'

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.addCleanup(cache.clear)
        Course.objects.create(name='Python', full_amount=30000)

    def _post(self, token=None, **body):
        if token:
            self.client.credentials(HTTP_X_INTAKE_TOKEN=token)
        return self.client.post(self.URL, {'name': 'Web', 'phone_number': '9800000888', **body}, format='json')

    @override_settings(LEAD_INTAKE_TOKEN='secret')
    def test_needs_the_token(self):
        self.assertEqual(self._post().status_code, 403)
        self.assertEqual(self._post(token='wrong').status_code, 403)
        self.assertEqual(self._post(token='secret').status_code, 201)

    @override_settings(LEAD_INTAKE_TOKEN='')
    def test_without_a_token_set_it_is_closed_outside_debug(self):
        self.assertEqual(self._post(token='anything').status_code, 403)

    @override_settings(LEAD_INTAKE_TOKEN='secret')
    def test_unknown_course_names_stay_text(self):
        from apps.course.matching import _CACHE

        _CACHE['public'] = []  # no public catalog in tests
        response = self._post(token='secret', course_interests='Python, Underwater Basket Weaving')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertFalse(Course.objects.filter(name__icontains='basket').exists())
        lead = Lead.objects.get(id=response.data['id'])
        rows = {(e.course.name if e.course else None, e.course_name) for e in lead.course_interests.all()}
        self.assertIn((None, 'Underwater Basket Weaving'), rows)
        self.assertEqual({name for name, _ in rows if name}, {'Python'})

    @override_settings(LEAD_INTAKE_TOKEN='secret')
    def test_rate_limited_per_address(self):
        from rest_framework.throttling import SimpleRateThrottle

        with mock.patch.object(SimpleRateThrottle, 'THROTTLE_RATES', {'lead_intake': '2/hour'}):
            codes = [self._post(token='secret', phone_number=f'98000009{i:02d}').status_code for i in range(3)]
        self.assertEqual(codes, [201, 201, 429])


class SignInRateLimitTests(APITestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.addCleanup(cache.clear)

    def test_sign_in_is_limited_per_email(self):
        from rest_framework.throttling import SimpleRateThrottle

        User.objects.create_user(email='a@test.com', name='A', password='right-pass')
        with mock.patch.object(SimpleRateThrottle, 'THROTTLE_RATES', {'login': '2/minute'}):
            codes = [
                self.client.post('/api/auth/login/', {'email': 'a@test.com', 'password': 'wrong'}).status_code
                for _ in range(3)
            ]
            other = self.client.post('/api/auth/login/', {'email': 'b@test.com', 'password': 'x'}).status_code
        self.assertEqual(codes[2], 429)
        self.assertNotEqual(other, 429)


class BranchScopeTests(SeatTestBase):
    """Branch users: their lists work, and what they add belongs to their branch."""

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import Permission
        from apps.branch.models import Branch

        self.ktm = Branch.objects.create(name='Kathmandu', code='KTM')
        self.pkr = Branch.objects.create(name='Pokhara', code='PKR')
        self.clerk = User.objects.create_user(email='ktm@test.com', name='Ktm', password='pass', branch=self.ktm)
        self.clerk.user_permissions.add(*Permission.objects.filter(codename__in=[
            'add_enrollment', 'change_enrollment', 'add_payment', 'add_followup', 'add_attendance',
        ]))

    def _branch_lead(self, branch):
        lead = self._lead()
        lead.branch = branch
        lead.save()
        return lead

    def _body(self, **extra):
        return {
            'course': self.course.id, 'enrollment_date': str(date.today()),
            'listed_fee': '30000', 'agreed_amount': '30000', **extra,
        }

    def test_notifications_and_attendance_lists_work(self):
        from apps.student.models import Attendance

        mine = Student.objects.create(full_name='Mine', phone='9800000201', branch=self.ktm)
        theirs = Student.objects.create(full_name='Theirs', phone='9800000202', branch=self.pkr)
        for student in (mine, theirs):
            Attendance.objects.create(student=student, date=date.today(), status='present')
        give_view_rights(self.clerk)
        self.client.force_authenticate(self.clerk)

        self.assertEqual(self.client.get('/api/notifications/').status_code, 200)
        response = self.client.get('/api/attendance/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r['student'] for r in response.data['results']], [mine.id])
        self.assertEqual(self.client.get(f'/api/students/{theirs.id}/attendance/').data['results'], [])

    def test_branch_user_records_take_their_branch(self):
        lead = self._branch_lead(self.ktm)
        self.client.force_authenticate(self.clerk)

        response = self.client.post(
            f'/api/leads/{lead.id}/enrollments/', self._body(branch=self.pkr.id), format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        enrollment = Enrollment.objects.get(id=response.data['id'])
        self.assertEqual(enrollment.branch, self.ktm)

        response = self.client.post(self._url(enrollment, 'payments/'), {
            'amount': '1000', 'payment_date': timezone.now().isoformat(), 'payment_method': 'cash',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Payment.objects.get(id=response.data['id']).branch, self.ktm)

        response = self.client.post(f'/api/leads/{lead.id}/followups/', {
            'scheduled_at': timezone.now().isoformat(), 'branch': self.pkr.id,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(FollowUp.objects.get(id=response.data['id']).branch, self.ktm)

        moved = self.client.patch(self._url(enrollment), {'branch': self.pkr.id}, format='json')
        self.assertEqual(moved.status_code, 400)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.branch, self.ktm)

    def test_other_branch_lead_and_student_are_not_found(self):
        lead = self._branch_lead(self.pkr)
        theirs = Student.objects.create(full_name='Theirs', phone='9800000203', branch=self.pkr)
        self.client.force_authenticate(self.clerk)

        self.assertEqual(
            self.client.post(f'/api/leads/{lead.id}/enrollments/', self._body(), format='json').status_code, 404,
        )
        self.assertEqual(self.client.post(f'/api/leads/{lead.id}/followups/', {
            'scheduled_at': timezone.now().isoformat(),
        }, format='json').status_code, 404)
        self.assertEqual(self.client.post(f'/api/students/{theirs.id}/attendance/', {
            'date': str(date.today()), 'status': 'present',
        }, format='json').status_code, 404)
        self.assertFalse(Enrollment.objects.filter(lead=lead).exists())

    def test_staff_pick_the_branch_or_get_the_leads(self):
        lead = self._branch_lead(self.pkr)
        response = self.client.post(f'/api/leads/{lead.id}/enrollments/', self._body(), format='json')
        self.assertEqual(Enrollment.objects.get(id=response.data['id']).branch, self.pkr)
        response = self.client.post(
            f'/api/leads/{lead.id}/enrollments/', self._body(branch=self.ktm.id), format='json',
        )
        self.assertEqual(Enrollment.objects.get(id=response.data['id']).branch, self.ktm)

    def test_backfill_fills_missing_branches(self):
        import importlib
        from django.apps import apps as django_apps

        lead = self._branch_lead(self.ktm)
        enrollment = Enrollment.objects.create(
            lead=lead, course=self.course, enrollment_date=date.today(), listed_fee=30000, agreed_amount=30000,
        )
        payment = Payment.objects.create(
            enrollment=enrollment, amount=1000, payment_method='cash', payment_date=timezone.now(),
        )
        followup = FollowUp.objects.create(lead=lead, scheduled_at=timezone.now())
        importlib.import_module('apps.lead.migrations.0036_fill_record_branches').fill_branches(django_apps, None)
        for row in (enrollment, payment, followup):
            row.refresh_from_db()
            self.assertEqual(row.branch, self.ktm)


class ActionPermissionTests(SeatTestBase):
    """Adding needs add_; changing an enrollment or payment needs change_."""

    def _user(self, *codenames):
        from django.contrib.auth.models import Permission

        user = User.objects.create_user(email=f'{"-".join(codenames)}@test.com', name='Clerk', password='pass')
        user.user_permissions.add(*Permission.objects.filter(codename__in=codenames))
        return user

    def test_add_only_cannot_change_state(self):
        enrollment = self._enroll(self.batch)
        self.client.force_authenticate(self._user('add_enrollment', 'add_payment'))
        self.assertEqual(self._confirm(enrollment).status_code, 403)
        for action in ('cancel/', 'status/', 'pause/'):
            response = self.client.post(self._url(enrollment, action), {'reason': 'x'}, format='json')
            self.assertEqual(response.status_code, 403, action)
        self.client.force_authenticate(self.admin)
        self._confirm(enrollment)
        payment = enrollment.payments.get()
        self.client.force_authenticate(self._user('add_payment'))
        response = self.client.post(self._url(enrollment, f'payments/{payment.id}/void/'), {'reason': 'x'}, format='json')
        self.assertEqual(response.status_code, 403)

    def test_change_permission_can(self):
        enrollment = self._enroll(self.batch)
        self.client.force_authenticate(self._user('change_enrollment'))
        self.assertEqual(self._confirm(enrollment).status_code, 200)
        response = self.client.post(self._url(enrollment, 'status/'), {'status': 'active', 'reason': 'x'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)


class NepalTimeTests(APITestCase):
    """Numbers and days follow Nepal time, not UTC."""

    def test_new_year_numbers_use_the_nepal_year(self):
        from datetime import datetime, timezone as dt_timezone

        # 00:45 on 1 Jan 2027 in Nepal is still 31 Dec 2026 in UTC.
        just_after_midnight = datetime(2026, 12, 31, 19, 0, tzinfo=dt_timezone.utc)
        with mock.patch('django.utils.timezone.now', return_value=just_after_midnight):
            lead = Lead.objects.create(full_name='New Year', phone='9800000999')
            self.assertEqual(timezone.localdate().isoformat(), '2027-01-01')
        self.assertTrue(lead.reference_number.startswith('LD-2027-'), lead.reference_number)


class LeadSummaryCardTests(APITestCase):
    """The lead list cards count every lead the user can see, not one page."""

    def setUp(self):
        self.admin = User.objects.create_user(email='cards@test.com', name='Admin', password='pass', is_staff=True)
        self.client.force_authenticate(self.admin)

    def _lead(self, n, **fields):
        lead = Lead.objects.create(full_name=f'Card {n}', phone=f'98000050{n:02d}')
        Lead.objects.filter(pk=lead.pk).update(**fields)
        return lead

    def test_counts(self):
        from apps.lead.filters import nepal_today_bounds

        now = timezone.now()
        today, tomorrow = nepal_today_bounds()
        month = today.replace(day=1)
        self._lead(1)  # new this week
        self._lead(2, created_at=now - timedelta(days=10))  # new last week
        self._lead(3, next_followup_at=today + timedelta(minutes=1))  # due today
        self._lead(4, next_followup_at=today - timedelta(minutes=1))  # overdue
        self._lead(5, next_followup_at=today - timedelta(days=2), is_converted=True)  # enrolled: not overdue
        self._lead(6, is_converted=True, converted_at=month + timedelta(minutes=1))
        self._lead(7, is_converted=True, converted_at=month - timedelta(days=3))
        self._lead(8, next_followup_at=tomorrow)  # upcoming

        data = self.client.get('/api/leads/summary/').data
        self.assertEqual(data['followups_today'], 1)
        self.assertEqual(data['followups_overdue'], 1)
        self.assertEqual(data['enrolled_this_month'], 1)
        self.assertEqual(data['enrolled_last_month'], 1)
        self.assertEqual(data['new_last_week'], 1)
        self.assertEqual(data['new_this_week'], 7)
