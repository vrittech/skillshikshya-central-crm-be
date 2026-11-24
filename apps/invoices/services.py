import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction
from django.utils import timezone

from apps.invoices.models import Invoice, InvoiceEmailConfig

logger = logging.getLogger(__name__)

DEFAULT_LOGO_URL = (
    'https://pub-3a712783614d44db9c5b349676a4cff8.r2.dev/uploads/converted_image.png'
)


def _recipient_for(student, enrollment):
    if student and student.email:
        return student.email
    lead = enrollment.lead if enrollment else None
    if lead and lead.email:
        return lead.email
    return None


def create_invoice(enrollment, invoice_type, amount, payment=None, created_by=None):
    """
    Create an Invoice for an enrollment and queue the email task.
    Returns (invoice, queued_email: bool).

    Never raises on email failures — the invoice is still persisted.
    """
    from apps.invoices.tasks import send_invoice_email_task

    enrollment_total_paid = enrollment.total_paid
    remaining = max(
        float(enrollment.agreed_amount) - float(enrollment_total_paid), 0,
    )

    invoice = Invoice.objects.create(
        enrollment=enrollment,
        student=enrollment.student,
        payment=payment,
        type=invoice_type,
        course_name=enrollment.course.name,
        amount=amount,
        total_paid_so_far=enrollment_total_paid,
        remaining_balance=remaining,
        branch=enrollment.branch,
        created_by=created_by,
    )

    recipient = _recipient_for(invoice.student, enrollment)
    if recipient:
        send_invoice_email_task.delay(invoice.id)
        queued = True
    else:
        logger.warning(
            'Invoice %s not emailed: student/lead has no email address.',
            invoice.invoice_number,
        )
        queued = False

    return invoice, queued


def build_email_recipients(invoice):
    """Returns (to_email, cc_list)."""
    config = InvoiceEmailConfig.load()
    cc = []
    if config.is_active:
        seen = set()
        for email in config.cc_emails or []:
            email = str(email).strip().lower()
            if email and email not in seen:
                seen.add(email)
                cc.append(email)
    return invoice.emailed_to, cc


def send_invoice_email(invoice):
    """Send the invoice email. Returns recipient or None."""
    from django.template.loader import render_to_string

    recipient = invoice.emailed_to or _recipient_for(invoice.student, invoice.enrollment)
    if not recipient:
        logger.warning('No recipient for invoice %s', invoice.invoice_number)
        return None

    config = InvoiceEmailConfig.load()
    _, cc_list = build_email_recipients(invoice)

    html = render_to_string('invoices/emails/invoice_email.html', {
        'invoice': invoice,
        'subject_prefix': config.subject_prefix,
        'logo_url': getattr(settings, 'INVOICE_LOGO_URL', DEFAULT_LOGO_URL),
    })

    subject = f'{config.subject_prefix} Invoice {invoice.invoice_number}'
    if invoice.type == Invoice.Type.PAYMENT:
        subject = f'{config.subject_prefix} Payment Receipt {invoice.invoice_number}'

    email = EmailMessage(
        subject=subject,
        body=html,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
        cc=cc_list,
    )
    email.content_subtype = 'html'
    email.send(fail_silently=False)

    invoice.emailed_to = recipient
    invoice.cc_emails = cc_list
    invoice.sent_at = timezone.now()
    invoice.save(update_fields=['emailed_to', 'cc_emails', 'sent_at'])
    return recipient


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
