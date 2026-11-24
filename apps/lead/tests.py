from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import ProtectedError
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.course.models import Course, CourseBatch
from apps.lead.models import Adjustment, Lead, Enrollment, Payment, EnrollmentHistory

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

    def test_price_cannot_be_edited_after_saving(self):
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

    def test_export_csv(self):
        response = self.client.get('/api/leads/export_csv/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Gita', response.content.decode())

    def test_notifications_url_cannot_purge_a_lead(self):
        response = self.client.post(f'/api/notifications/{self.lead.id}/purge/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(Lead._base_manager.filter(id=self.lead.id).exists())
