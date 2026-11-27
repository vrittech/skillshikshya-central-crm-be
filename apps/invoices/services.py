from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.invoices.models import Invoice, InvoiceEmailConfig


def create_invoice(enrollment, invoice_type, amount, payment=None, created_by=None):
    """Make an invoice (course fee) or a receipt (payment) for an enrollment.

    The numbers are kept as they are now, so the invoice reads the same later.
    No email is sent here: the caller sends the email it goes with (see
    apps.lead.emails).
    """
    # Decimal, not float, and the same math as the enrollment card: money
    # entries count, and refunds are taken off what was paid.
    paid_so_far = Decimal(enrollment.total_paid) - enrollment.refunds_total
    remaining = max(enrollment.remaining_amount, Decimal('0'))

    return Invoice.objects.create(
        enrollment=enrollment,
        student=enrollment.student,
        payment=payment,
        type=invoice_type,
        course_name=enrollment.course.name,
        amount=amount,
        total_paid_so_far=paid_so_far,
        remaining_balance=remaining,
        branch=enrollment.branch,
        created_by=created_by,
    )


def invoice_cc():
    """Addresses copied on every invoice and receipt email, when turned on."""
    config = InvoiceEmailConfig.load()
    cc = []
    if config.is_active:
        seen = set()
        for email in config.cc_emails or []:
            email = str(email).strip().lower()
            if email and email not in seen:
                seen.add(email)
                cc.append(email)
    return cc


def build_email_recipients(invoice):
    """Returns (to_email, cc_list)."""
    return invoice.emailed_to, invoice_cc()


@transaction.atomic
def cancel_invoice(invoice, *, reason, user):
    """Cancel an invoice with a reason and note it in the enrollment's history."""
    from apps.lead.models import EnrollmentHistory
    from apps.lead.services import ActionRefused

    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status == Invoice.Status.CANCELLED:
        raise ActionRefused('This invoice is already cancelled.')
    invoice.status = Invoice.Status.CANCELLED
    invoice.cancel_reason = reason
    invoice.cancelled_by = user
    invoice.cancelled_at = timezone.now()
    invoice.save(update_fields=['status', 'cancel_reason', 'cancelled_by', 'cancelled_at'])
    EnrollmentHistory.record(
        enrollment=invoice.enrollment,
        action=EnrollmentHistory.Action.CANCEL_INVOICE,
        reason=reason,
        performed_by=user,
        before={'invoice': invoice.invoice_number, 'status': Invoice.Status.ACTIVE},
        after={'invoice': invoice.invoice_number, 'status': invoice.status},
    )
    return invoice
