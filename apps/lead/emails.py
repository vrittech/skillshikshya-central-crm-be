"""The emails sent about leads and students, and who gets each one.

- Lead assigned: to the counselor the lead is given to. An import sends
  one email per counselor listing all the leads they got.
- Enrollment received: to the lead, when an enrollment is saved as pending.
- Enrollment confirmed: to the student, with the fee invoice attached.
- Payment received: to the student, with the receipt attached.
- Invoice: an invoice or receipt sent again.
- Teams account: the student's Teams sign-in, sent straight away. The
  password is in the email only; the log keeps it hidden.

No email goes out when a lead is created. Each one is logged in EmailLog
(see apps.global_config.mailer) and shows on the lead's History tab.
"""
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings

from apps.global_config.mailer import queue_email, send_now
from apps.global_config.models import EmailLog

NEPAL = ZoneInfo('Asia/Kathmandu')

# Sent a little later, so "Save and confirm" sends only the confirmation.
PENDING_DELAY_SECONDS = 120


def npr(amount):
    amount = Decimal(amount or 0)
    text = f'{amount:,.2f}'
    return f'NPR {text[:-3] if text.endswith(".00") else text}'


def day(value):
    return value.strftime('%-d %b %Y') if value else ''


def when(value):
    return value.astimezone(NEPAL).strftime('%-d %b %Y, %-I:%M %p') if value else ''


def _time(value):
    return value.strftime('%-I:%M %p') if value else ''


def _frontend():
    return (getattr(settings, 'FRONTEND_URL', '') or '').rstrip('/')


def recipient(enrollment):
    """The student's email, else the lead's."""
    student = enrollment.student
    if student and student.email:
        return student.email
    return enrollment.lead.email or ''


def _name(enrollment):
    return enrollment.student.full_name if enrollment.student else enrollment.lead.full_name


def _invoice_cc():
    """Addresses copied on invoices and receipts (Invoices › Email settings)."""
    from apps.invoices.services import invoice_cc

    return invoice_cc()


def batch_rows(enrollment):
    batch = enrollment.current_batch or enrollment.batch
    if batch is None:
        return [('Batch', 'To be assigned')]
    shift = batch.batch
    times = ''
    if shift and shift.start_time and shift.end_time:
        times = f'{_time(shift.start_time)} – {_time(shift.end_time)}'
    return [
        ('Batch', batch.name),
        ('Class time', f'{shift.name}, {times}' if shift and times else (shift.name if shift else '')),
        ('Starts on', day(batch.start_date)),
        ('Mode', batch.get_mode_display()),
    ]


def fee_rows(enrollment):
    discount = Decimal(enrollment.standard_discount or 0) + Decimal(enrollment.additional_discount or 0)
    return [
        ('Course fee', npr(enrollment.listed_fee)),
        ('Discount', npr(discount) if discount else ''),
        ('Amount to pay', npr(enrollment.agreed_amount)),
    ]


def counselor_of(lead):
    counselor = lead.assigned_counselor
    if counselor is None:
        return None
    return {
        'name': counselor.name or counselor.email,
        'email': counselor.email,
        'phone': getattr(counselor, 'phone', '') or '',
    }


def lead_assigned(lead, *, user=None):
    """To the counselor the lead was given to. Not when they gave it to themselves."""
    counselor = lead.assigned_counselor
    if counselor is None or (user is not None and user.pk == counselor.pk):
        return None
    course = lead.primary_course.name if lead.primary_course_id else ''
    return queue_email(
        EmailLog.Kind.LEAD_ASSIGNED,
        to=counselor.email,
        subject=f'New lead assigned: {lead.full_name}',
        template='lead/emails/lead_assigned.html',
        context={
            'counselor_name': counselor.name or counselor.email,
            'lead': lead,
            'lead_url': f'{_frontend()}/leads/{lead.pk}',
            'assigned_by': (user.name or user.email) if user else '',
            'rows': [
                ('Name', lead.full_name),
                ('Reference', lead.reference_number),
                ('Phone', lead.phone),
                ('Email', lead.email),
                ('Course', course),
                ('Source', lead.source.name if lead.source_id else ''),
                ('Branch', lead.branch.name if lead.branch_id else ''),
            ],
        },
        lead=lead, branch=lead.branch, user=user,
    )



def leads_assigned(counselor, leads, *, user=None):
    """To a counselor given several leads at once (an import): one email for all."""
    if user is not None and user.pk == counselor.pk:
        return None
    shown = leads[:50]
    return queue_email(
        EmailLog.Kind.LEAD_ASSIGNED,
        to=counselor.email,
        subject=f'{len(leads)} new leads assigned to you',
        template='lead/emails/leads_assigned.html',
        context={
            'counselor_name': counselor.name or counselor.email,
            'count': len(leads),
            'leads': [
                {
                    'name': lead.full_name,
                    'phone': lead.phone or '',
                    'course': lead.primary_course.name if lead.primary_course_id else '',
                    'url': f'{_frontend()}/leads/{lead.pk}',
                }
                for lead in shown
            ],
            'more': len(leads) - len(shown),
            'leads_url': f'{_frontend()}/leads',
            'assigned_by': (user.name or user.email) if user else '',
        },
        user=user,
    )

def enrollment_received(enrollment, *, user=None):
    """To the lead, when an enrollment is saved as pending."""
    lead = enrollment.lead
    return queue_email(
        EmailLog.Kind.ENROLLMENT_PENDING,
        to=recipient(enrollment),
        subject=f'Enrollment received: {enrollment.course.name}',
        template='lead/emails/enrollment_received.html',
        context={
            'name': _name(enrollment),
            'course': enrollment.course.name,
            'rows': [('Course', enrollment.course.name), *batch_rows(enrollment), *fee_rows(enrollment)],
            'counselor': counselor_of(lead),
        },
        lead=lead, enrollment=enrollment, branch=enrollment.branch, user=user,
        countdown=PENDING_DELAY_SECONDS,
    )


def _schedule(enrollment):
    from apps.lead.services import due_rows

    return [
        {'due_on': day(r['due_on']) or 'On joining', 'amount': npr(r['amount']), 'left': npr(r['left']),
         'paid': r['status'] == 'paid'}
        for r in due_rows(enrollment)
    ]


def enrollment_confirmed(enrollment, invoice, *, user=None):
    """To the student, when the enrollment is confirmed. The fee invoice goes with it."""
    student = enrollment.student
    paid = Decimal(enrollment.total_paid) - enrollment.refunds_total
    return queue_email(
        EmailLog.Kind.ENROLLMENT_CONFIRMED,
        to=recipient(enrollment),
        subject=f'Enrollment confirmed: {enrollment.course.name}',
        template='lead/emails/enrollment_confirmed.html',
        context={
            'name': _name(enrollment),
            'course': enrollment.course.name,
            'invoice': invoice,
            'rows': [
                ('Student ID', student.student_id if student else ''),
                ('Course', enrollment.course.name),
                *batch_rows(enrollment),
                ('Branch', enrollment.branch.name if enrollment.branch_id else ''),
            ],
            'money_rows': [
                *fee_rows(enrollment),
                ('Paid so far', npr(paid) if paid else ''),
                ('Balance', npr(max(enrollment.remaining_amount, Decimal('0')))),
            ],
            'schedule': _schedule(enrollment),
            'counselor': counselor_of(enrollment.lead),
        },
        lead=enrollment.lead, enrollment=enrollment, invoice=invoice, cc=_invoice_cc(),
        branch=enrollment.branch, user=user,
    )


def _next_due(enrollment):
    from apps.lead.services import due_rows

    for row in due_rows(enrollment):
        if row['left'] > 0:
            return row
    return None


def payment_received(payment, receipt, *, user=None):
    """To the student, when a payment is received. The receipt goes with it."""
    enrollment = payment.enrollment
    next_due = _next_due(enrollment)
    return queue_email(
        EmailLog.Kind.PAYMENT_RECEIVED,
        to=recipient(enrollment),
        subject=f'Payment received: {npr(payment.amount)} for {enrollment.course.name}',
        template='lead/emails/payment_received.html',
        context={
            'name': _name(enrollment),
            'course': enrollment.course.name,
            'amount': npr(payment.amount),
            'receipt': receipt,
            'rows': [
                ('Receipt number', receipt.invoice_number if receipt else ''),
                ('Amount', npr(payment.amount)),
                ('Paid on', when(payment.payment_date)),
                ('Method', payment.get_payment_method_display()),
                ('Reference', payment.transaction_reference or ''),
                ('Total paid', npr(Decimal(enrollment.total_paid) - enrollment.refunds_total)),
                ('Balance', npr(max(enrollment.remaining_amount, Decimal('0')))),
            ],
            'next_due': {
                'amount': npr(next_due['left']), 'due_on': day(next_due['due_on']),
            } if next_due else None,
        },
        lead=enrollment.lead, enrollment=enrollment, invoice=receipt, cc=_invoice_cc(),
        branch=enrollment.branch, user=user,
    )


def invoice_again(invoice, *, user=None):
    """An invoice or receipt sent again, with its PDF."""
    enrollment = invoice.enrollment
    is_receipt = invoice.type == 'payment'
    label = 'Receipt' if is_receipt else 'Invoice'
    return queue_email(
        EmailLog.Kind.INVOICE,
        to=invoice.emailed_to or recipient(enrollment),
        subject=f'{label} {invoice.invoice_number}: {invoice.course_name}',
        template='lead/emails/invoice.html',
        context={
            'name': _name(enrollment),
            'invoice': invoice,
            'label': label,
            'rows': [
                (f'{label} number', invoice.invoice_number),
                ('Date', day(invoice.created_at.astimezone(NEPAL))),
                ('Course', invoice.course_name),
                ('Amount', npr(invoice.amount)),
                ('Balance', npr(invoice.remaining_balance)),
            ],
        },
        lead=enrollment.lead, enrollment=enrollment, invoice=invoice, cc=_invoice_cc(),
        branch=enrollment.branch, user=user,
    )


TEAMS_SIGN_IN_URL = 'https://teams.microsoft.com'
HIDDEN = '\u2022' * 8


def teams_details(licence, password, *, user=None):
    """To the student: their Teams sign-in. Sent now, never queued, password not kept."""
    enrollment = licence.enrollment
    context = {
        'name': _name(enrollment),
        'course': enrollment.course.name,
        'sign_in_url': TEAMS_SIGN_IN_URL,
        'account': licence.account,
        'password': password,
        'rows': [('Course', enrollment.course.name), *batch_rows(enrollment)],
        'counselor': counselor_of(enrollment.lead),
    }
    return send_now(
        EmailLog.Kind.TEAMS_ACCOUNT,
        to=recipient(enrollment),
        subject=f'Your Microsoft Teams account for {enrollment.course.name}',
        template='lead/emails/teams_account.html',
        context=context,
        hidden_context={'password': HIDDEN},
        lead=enrollment.lead, enrollment=enrollment, branch=enrollment.branch, user=user,
    )
