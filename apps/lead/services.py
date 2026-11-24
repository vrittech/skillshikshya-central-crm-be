"""Enrollment and money actions that must leave a reason trail.

Each action changes the data and writes an EnrollmentHistory row inside one
transaction, so a change can never be saved without its reason. Views only
check the request and call these functions.
"""
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException

from apps.lead.models import Adjustment, Enrollment, EnrollmentHistory, Payment


class ActionRefused(APIException):
    """400 with a plain {"detail": "..."} body, for actions that can't run now."""

    status_code = 400
    default_detail = 'This action is not allowed.'
    default_code = 'action_refused'


def _lock(instance):
    """Re-read the row with a lock, so two people can't act on it at once."""
    return type(instance).objects.select_for_update().get(pk=instance.pk)


@transaction.atomic
def cancel_enrollment(enrollment, *, reason, user):
    enrollment = _lock(enrollment)
    if enrollment.status == Enrollment.Status.CANCELLED:
        raise ActionRefused('This enrollment is already cancelled.')
    old_status = enrollment.status
    enrollment.status = Enrollment.Status.CANCELLED
    enrollment.updated_by = user
    enrollment.save(update_fields=['status', 'updated_by', 'updated_at'])
    EnrollmentHistory.record(
        enrollment=enrollment,
        action=EnrollmentHistory.Action.CANCEL,
        reason=reason,
        performed_by=user,
        before={'status': old_status},
        after={'status': enrollment.status},
    )
    return enrollment


@transaction.atomic
def reinstate_enrollment(enrollment, *, reason, user):
    """Undo a cancel: the enrollment goes back to the status it had before."""
    enrollment = _lock(enrollment)
    if enrollment.status != Enrollment.Status.CANCELLED:
        raise ActionRefused('Only a cancelled enrollment can be reinstated.')
    last_cancel = enrollment.history.filter(
        action=EnrollmentHistory.Action.CANCEL,
    ).order_by('-created_at', '-id').first()
    restored = (last_cancel.before.get('status') if last_cancel else None) or (
        Enrollment.Status.ACTIVE
    )
    enrollment.status = restored
    enrollment.updated_by = user
    enrollment.save(update_fields=['status', 'updated_by', 'updated_at'])
    EnrollmentHistory.record(
        enrollment=enrollment,
        action=EnrollmentHistory.Action.REINSTATE,
        reason=reason,
        performed_by=user,
        before={'status': Enrollment.Status.CANCELLED},
        after={'status': restored},
    )
    return enrollment


def issue_receipt(payment, *, user):
    """Make the receipt for a successful payment, once."""
    from apps.invoices.services import create_invoice

    if payment.status != Payment.Status.SUCCESSFUL or payment.invoices.exists():
        return None
    invoice, _ = create_invoice(
        enrollment=payment.enrollment,
        invoice_type='payment',
        amount=payment.amount,
        payment=payment,
        created_by=user,
    )
    return invoice


@transaction.atomic
def confirm_payment(payment, *, user):
    """Pending -> successful (the money arrived). Issues the receipt."""
    payment = _lock(payment)
    if payment.status != Payment.Status.PENDING:
        raise ActionRefused('Only a pending payment can be confirmed.')
    payment.status = Payment.Status.SUCCESSFUL
    payment.save(update_fields=['status'])
    issue_receipt(payment, user=user)
    return payment


@transaction.atomic
def void_payment(payment, *, reason, user):
    """A wrong payment is voided, never edited or deleted. Record a new one if needed."""
    payment = _lock(payment)
    if payment.status not in (Payment.Status.PENDING, Payment.Status.SUCCESSFUL):
        raise ActionRefused(
            f'A {payment.get_status_display().lower()} payment can\'t be voided.'
        )
    old_status = payment.status
    payment.status = Payment.Status.VOIDED
    payment.void_reason = reason
    payment.voided_by = user
    payment.voided_at = timezone.now()
    payment.save(update_fields=['status', 'void_reason', 'voided_by', 'voided_at'])
    EnrollmentHistory.record(
        enrollment=payment.enrollment,
        action=EnrollmentHistory.Action.VOID_PAYMENT,
        reason=reason,
        performed_by=user,
        before={'payment': payment.id, 'amount': str(payment.amount), 'status': old_status},
        after={'payment': payment.id, 'amount': str(payment.amount), 'status': payment.status},
    )
    return payment


@transaction.atomic
def add_adjustment(enrollment, *, kind, amount, reason, user):
    """Add an extra charge, discount, write-off or refund, with a history row.

    A refund can't be more than what was paid and not yet refunded. A write-off
    can't be more than what is owed. Discounts may go below 0 (a credit we owe
    the student, e.g. after a switch to a cheaper course), to be refunded.
    """
    enrollment = _lock(enrollment)
    balance_before = enrollment.remaining_amount
    if kind == Adjustment.Kind.REFUND:
        refundable = enrollment.total_paid - enrollment.refunds_total
        if amount > refundable:
            raise ActionRefused(
                f'Only {refundable} can be refunded: that is what was paid '
                f'and not refunded yet.'
            )
    if kind == Adjustment.Kind.WRITE_OFF and amount > balance_before:
        raise ActionRefused(
            f'Only {max(balance_before, 0)} is owed, so no more than that can be written off.'
        )
    adjustment = Adjustment.objects.create(
        enrollment=enrollment,
        kind=kind,
        amount=amount,
        reason=reason,
        branch=enrollment.branch,
        created_by=user,
    )
    EnrollmentHistory.record(
        enrollment=enrollment,
        action=kind,  # the four kinds share their names with history actions
        reason=reason,
        performed_by=user,
        before={'amount': str(amount), 'balance': str(balance_before)},
        after={'amount': str(amount), 'balance': str(enrollment.remaining_amount)},
    )
    return adjustment
