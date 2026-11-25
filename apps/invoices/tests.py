from datetime import date
from decimal import Decimal

from django.contrib import admin as django_admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from django.test import override_settings

from apps.branch.models import Branch
from apps.course.models import Course
from apps.invoices.models import Invoice, InvoiceEmailConfig
from apps.invoices.services import create_invoice
from apps.lead.models import Adjustment, Enrollment, EnrollmentHistory, Lead, Payment

User = get_user_model()


@override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=False)
class InvoiceTestBase(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email='admin@test.com', name='Admin', password='pass', is_staff=True,
            call_seconds_balance=100 * 60,
        )
        self.client.force_authenticate(self.admin)
        self.branch = Branch.objects.create(name='Kathmandu', code='KTM')
        self.course = Course.objects.create(name='Python Advanced')
        self.lead = Lead.objects.create(
            full_name='Rahul Sharma', phone='9800000002',
            email='rahul@example.com',
        )
        self.enrollment = Enrollment.objects.create(
            lead=self.lead,
            course=self.course,
            enrollment_date=date.today(),
            listed_fee=30000,
            agreed_amount=28000,
            status='active',
        )
        InvoiceEmailConfig.load()


class FeeInvoiceOnConfirmTests(InvoiceTestBase):
    def setUp(self):
        super().setUp()
        Enrollment.objects.filter(id=self.enrollment.id).update(status='pending')
        self.confirm_url = f'/api/leads/{self.lead.id}/enrollments/{self.enrollment.id}/confirm/'
        self.first_payment = {'first_payment': {'amount': '5000', 'payment_method': 'cash'}}

    def test_confirm_generates_enrollment_invoice(self):
        response = self.client.post(self.confirm_url, self.first_payment, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        invoice = Invoice.objects.get(type='enrollment')
        self.enrollment.refresh_from_db()
        self.assertEqual(invoice.enrollment_id, self.enrollment.id)
        self.assertEqual(str(invoice.amount), '28000.00')
        self.assertEqual(invoice.course_name, 'Python Advanced')
        self.assertTrue(invoice.invoice_number.startswith('INV-'))
        self.assertEqual(invoice.student_id, self.enrollment.student_id)

    def test_no_enrollment_means_no_invoice(self):
        Lead.objects.create(full_name='No Course', phone='111')
        response = self.client.post('/api/students/', {
            'full_name': 'Standalone Student',
            'phone': '222',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Invoice.objects.count(), 0)

    def test_no_duplicate_fee_invoice(self):
        self.client.post(self.confirm_url, self.first_payment, format='json')
        self.client.post(self.confirm_url, self.first_payment, format='json')
        self.assertEqual(Invoice.objects.filter(type='enrollment').count(), 1)


class PaymentInvoiceTests(InvoiceTestBase):
    def _pay(self, amount, pay_status='successful'):
        return self.client.post(
            f'/api/leads/{self.lead.id}/enrollments/{self.enrollment.id}/payments/',
            {
                'amount': amount,
                'payment_date': timezone.now().isoformat(),
                'payment_method': 'cash',
                'status': pay_status,
            },
            format='json',
        )

    def test_successful_payment_creates_receipt(self):
        response = self._pay('14000')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        invoice = Invoice.objects.get(type='payment')
        self.assertEqual(invoice.payment.amount, 14000)
        self.assertEqual(str(invoice.total_paid_so_far), '14000.00')
        self.assertEqual(str(invoice.remaining_balance), '14000.00')

    def test_pending_payment_creates_nothing(self):
        self._pay('5000', pay_status='pending')
        self.assertEqual(Invoice.objects.count(), 0)

    def test_confirming_pending_payment_generates_receipt_once(self):
        response = self._pay('5000', pay_status='pending')
        payment_id = response.data['id']
        self.assertEqual(Invoice.objects.count(), 0)

        url = (
            f'/api/leads/{self.lead.id}/enrollments/{self.enrollment.id}'
            f'/payments/{payment_id}/confirm/'
        )
        first = self.client.post(url, {}, format='json')
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(Invoice.objects.filter(type='payment').count(), 1)
        receipt = Invoice.objects.get(type='payment')
        self.assertEqual(receipt.payment_id, payment_id)

        # confirming again is refused and must not duplicate the receipt
        second = self.client.post(url, {}, format='json')
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Invoice.objects.filter(type='payment').count(), 1)

    def test_successful_payment_cannot_be_edited(self):
        response = self._pay('14000')
        payment_id = response.data['id']
        url = (
            f'/api/leads/{self.lead.id}/enrollments/{self.enrollment.id}'
            f'/payments/{payment_id}/'
        )
        patched = self.client.patch(url, {'amount': '15000.00'}, format='json')
        self.assertEqual(patched.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(Payment.objects.get(id=payment_id).amount, 14000)
        self.assertEqual(Invoice.objects.count(), 1)


class InvoiceEndpointTests(InvoiceTestBase):
    def test_list_invoices(self):
        Invoice.objects.create(
            enrollment=self.enrollment, type='enrollment',
            course_name='X', amount=28000,
        )
        response = self.client.get('/api/invoices/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)

    def test_resend_requires_staff(self):
        invoice = Invoice.objects.create(
            enrollment=self.enrollment, type='enrollment',
            course_name='X', amount=28000,
        )
        counselor = User.objects.create_user(email='c@t.com', name='C', password='x')
        self.client.force_authenticate(counselor)
        response = self.client.post(f'/api/invoices/{invoice.id}/resend/')
        self.assertIn(response.status_code, (403, 401))

    def test_resend_queues_the_email_with_the_pdf(self):
        from apps.global_config.models import EmailLog

        self.enrollment.lead.email = 'student@example.com'
        self.enrollment.lead.save(update_fields=['email'])
        invoice = Invoice.objects.create(
            enrollment=self.enrollment, type='enrollment',
            course_name='X', amount=28000,
        )
        response = self.client.post(f'/api/invoices/{invoice.id}/resend/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        log = EmailLog.objects.get(kind='invoice')
        self.assertEqual((log.to_email, log.invoice_id, log.status), ('student@example.com', invoice.id, 'queued'))

    def test_email_config_get_put_staff_only(self):
        response = self.client.put('/api/invoices/email-config/', {
            'cc_emails': ['accounts@skill.com'],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        config = InvoiceEmailConfig.load()
        self.assertEqual(config.cc_emails, ['accounts@skill.com'])

        counselor = User.objects.create_user(email='c2@t.com', name='C2', password='x')
        self.client.force_authenticate(counselor)
        denied = self.client.get('/api/invoices/email-config/')
        self.assertIn(denied.status_code, (403, 401))

class InvoiceBranchScopingTests(InvoiceTestBase):
    def test_branch_scoping_on_invoice_list(self):
        from apps.invoices.models import Invoice as Inv
        branch2 = Branch.objects.create(name='Pokhara', code='PKR')
        inv1 = Inv.objects.create(
            enrollment=self.enrollment, type='enrollment',
            course_name='X', amount=28000, branch=self.branch,
        )
        enrollment2 = Enrollment.objects.create(
            lead=Lead.objects.create(full_name='Other', phone='2'),
            course=self.course, enrollment_date=date.today(),
            listed_fee=100, agreed_amount=100, status='active',
            branch=branch2,
        )
        Inv.objects.create(
            enrollment=enrollment2, type='enrollment',
            course_name='Y', amount=100, branch=branch2,
        )

        # staff sees all
        response = self.client.get('/api/invoices/')
        self.assertEqual(response.data['count'], 2)

        # staff filtered by branch_id
        filtered = self.client.get(f'/api/invoices/?branch_id={self.branch.id}')
        self.assertEqual(filtered.data['count'], 1)
        self.assertEqual(filtered.data['results'][0]['id'], inv1.id)

        # non-staff locked to their own branch
        counselor = User.objects.create_user(
            email='br@t.com', name='BR', password='x', branch=self.branch,
        )
        self.client.force_authenticate(counselor)
        scoped = self.client.get('/api/invoices/')
        self.assertEqual(scoped.data['count'], 1)
        self.assertEqual(scoped.data['results'][0]['branch'], self.branch.id)

    def test_new_invoices_snapshot_enrollment_branch(self):
        lead3 = Lead.objects.create(
            full_name='Branchy', phone='3',
        )
        enr = Enrollment.objects.create(
            lead=lead3, course=self.course, enrollment_date=date.today(),
            listed_fee=10, agreed_amount=10, status='active',
            branch=self.branch,
        )
        invoice = create_invoice(enrollment=enr, invoice_type='enrollment', amount=10)
        self.assertEqual(invoice.branch_id, self.branch.id)


class InvoiceCancelTests(InvoiceTestBase):
    def _invoice(self):
        return Invoice.objects.create(
            enrollment=self.enrollment, type='enrollment',
            course_name='Python Advanced', amount=28000,
        )

    def test_cancel_with_reason(self):
        invoice = self._invoice()
        response = self.client.post(
            f'/api/invoices/{invoice.id}/cancel/', {'reason': 'Wrong amount'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'cancelled')
        self.assertEqual(response.data['cancelled_by_name'], 'Admin')
        invoice.refresh_from_db()
        self.assertEqual(invoice.cancel_reason, 'Wrong amount')
        self.assertIsNotNone(invoice.cancelled_at)
        row = self.enrollment.history.get()
        self.assertEqual(row.action, 'cancel_invoice')
        self.assertEqual(row.after, {'invoice': invoice.invoice_number, 'status': 'cancelled'})

    def test_cancel_needs_reason_and_happens_once(self):
        invoice = self._invoice()
        url = f'/api/invoices/{invoice.id}/cancel/'
        self.assertEqual(self.client.post(url, {}, format='json').status_code, 400)
        self.assertEqual(self.client.post(url, {'reason': 'Wrong'}, format='json').status_code, 200)
        again = self.client.post(url, {'reason': 'Again'}, format='json')
        self.assertEqual(again.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.enrollment.history.count(), 1)

    def test_cancel_is_staff_only(self):
        invoice = self._invoice()
        counselor = User.objects.create_user(email='c3@t.com', name='C3', password='x')
        self.client.force_authenticate(counselor)
        response = self.client.post(
            f'/api/invoices/{invoice.id}/cancel/', {'reason': 'Wrong'}, format='json',
        )
        self.assertIn(response.status_code, (403, 401))

    def test_cancelled_invoice_is_not_resent(self):
        from apps.global_config.models import EmailLog

        invoice = self._invoice()
        self.client.post(f'/api/invoices/{invoice.id}/cancel/', {'reason': 'Wrong'}, format='json')
        response = self.client.post(f'/api/invoices/{invoice.id}/resend/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(EmailLog.objects.filter(kind='invoice').exists())

    def test_voiding_a_payment_cancels_its_receipt(self):
        payments_url = f'/api/leads/{self.lead.id}/enrollments/{self.enrollment.id}/payments/'
        payment = self.client.post(payments_url, {
            'amount': '5000', 'payment_date': timezone.now().isoformat(),
            'payment_method': 'cash', 'status': 'successful',
        }, format='json').data
        receipt = Invoice.objects.get(type='payment', payment_id=payment['id'])
        self.client.post(
            f"{payments_url}{payment['id']}/void/", {'reason': 'Duplicate'}, format='json',
        )
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, 'cancelled')
        self.assertEqual(receipt.cancel_reason, 'Payment voided: Duplicate')
        actions = set(self.enrollment.history.values_list('action', flat=True))
        self.assertEqual(actions, {'void_payment', 'cancel_invoice'})

    def test_filter_by_status(self):
        self._invoice()
        cancelled = self._invoice()
        self.client.post(f'/api/invoices/{cancelled.id}/cancel/', {'reason': 'x'}, format='json')
        response = self.client.get('/api/invoices/?status=cancelled')
        self.assertEqual([r['id'] for r in response.data['results']], [cancelled.id])


class CreateInvoiceMoneyTests(InvoiceTestBase):
    def test_remaining_counts_money_entries_in_decimal(self):
        Payment.objects.create(
            enrollment=self.enrollment, amount=Decimal('10000.50'),
            payment_date=timezone.now(), payment_method='cash', status='successful',
        )
        Adjustment.objects.create(
            enrollment=self.enrollment, kind='discount', amount=Decimal('1000.25'),
            reason='Scholarship', created_by=self.admin,
        )
        invoice = create_invoice(self.enrollment, 'enrollment', self.enrollment.agreed_amount)
        invoice.refresh_from_db()
        expected = self.enrollment.agreed_amount - Decimal('10000.50') - Decimal('1000.25')
        self.assertEqual(invoice.remaining_balance, expected)
        self.assertEqual(invoice.total_paid_so_far, Decimal('10000.50'))

    def test_credit_shows_as_zero_remaining(self):
        Adjustment.objects.create(
            enrollment=self.enrollment, kind='discount',
            amount=self.enrollment.agreed_amount + 500, reason='Waived', created_by=self.admin,
        )
        invoice = create_invoice(self.enrollment, 'enrollment', self.enrollment.agreed_amount)
        invoice.refresh_from_db()
        self.assertEqual(invoice.remaining_balance, Decimal('0'))


class ReadOnlyMoneyAdminTests(InvoiceTestBase):
    def setUp(self):
        super().setUp()
        self.superuser = User.objects.create_superuser(
            email='root@t.com', name='Root', password='x',
        )
        self.client.force_login(self.superuser)

    def test_money_models_are_view_only(self):
        request = RequestFactory().get('/admin/')
        request.user = self.superuser
        for model in (Enrollment, Payment, Adjustment, EnrollmentHistory, Invoice):
            model_admin = django_admin.site._registry[model]
            self.assertFalse(model_admin.has_add_permission(request), model)
            self.assertFalse(model_admin.has_change_permission(request), model)
            self.assertFalse(model_admin.has_delete_permission(request), model)
            self.assertTrue(model_admin.has_view_permission(request), model)

    def test_admin_pages(self):
        payment = Payment.objects.create(
            enrollment=self.enrollment, amount=500,
            payment_date=timezone.now(), payment_method='cash',
        )
        self.assertEqual(self.client.get('/admin/lead/payment/').status_code, 200)
        self.assertEqual(self.client.get(f'/admin/lead/payment/{payment.id}/change/').status_code, 200)
        self.assertEqual(self.client.get('/admin/lead/payment/add/').status_code, 403)
        self.assertEqual(self.client.get(f'/admin/lead/payment/{payment.id}/delete/').status_code, 403)
        self.assertEqual(self.client.get('/admin/invoices/invoice/').status_code, 200)
