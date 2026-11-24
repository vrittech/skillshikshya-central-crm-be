"""Enrollment and money actions that must leave a reason trail.

Each action changes the data and writes an EnrollmentHistory row inside one
transaction, so a change can never be saved without its reason. Views only
check the request and call these functions.
"""
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException

from apps.course.models import CourseBatch
from apps.lead.models import Adjustment, Enrollment, EnrollmentHistory, Lead, Payment, Placement


class ActionRefused(APIException):
    """400 with a plain {"detail": "..."} body, for actions that can't run now."""

    status_code = 400
    default_detail = 'This action is not allowed.'
    default_code = 'action_refused'


def _lock(instance):
    """Re-read the row with a lock, so two people can't act on it at once."""
    return type(instance).objects.select_for_update().get(pk=instance.pk)


# ---------------------------------------------------------------- seats


def _take_seat(enrollment, batch, *, kind, reason, user, on=None, from_batch=None):
    """Seat the (locked) enrollment in a batch and add a seat history row.

    The batch row is locked while its seats are counted, so two people can't
    take its last seat at the same time. The first batch an enrollment joins
    becomes its money batch, if it has none yet.
    """
    batch = CourseBatch.objects.select_for_update().get(pk=batch.pk)
    if batch.course_id != enrollment.course_id:
        raise ActionRefused(
            f'{batch.name} is a batch of another course. To switch course, cancel this '
            'enrollment with a reason and add a new one.'
        )
    if batch.status == CourseBatch.Status.CLOSED:
        raise ActionRefused(f'{batch.name} is closed. Pick an upcoming or running batch.')
    if batch.capacity is not None:
        taken = batch.count_seats_taken(exclude=enrollment)
        if taken >= batch.capacity:
            raise ActionRefused(
                f'{batch.name} is full ({taken} of {batch.capacity} seats). '
                'Pick another batch or add seats to it.'
            )
    Placement.objects.create(
        enrollment=enrollment,
        course_batch=batch,
        from_batch=from_batch,
        kind=kind,
        started_on=on or timezone.localdate(),
        reason=reason,
        created_by=user,
    )
    enrollment.current_batch = batch
    fields = ['current_batch', 'updated_at']
    if enrollment.batch_id is None:
        enrollment.batch = batch
        fields.append('batch')
    enrollment.save(update_fields=fields)
    return batch


def _free_seat(enrollment, *, kind, reason, user, on=None):
    """Take the (locked) enrollment out of its batch and add a seat history row."""
    left = enrollment.current_batch
    Placement.objects.create(
        enrollment=enrollment,
        course_batch=None,
        from_batch=left,
        kind=kind,
        started_on=on or timezone.localdate(),
        reason=reason,
        created_by=user,
    )
    enrollment.current_batch = None
    enrollment.save(update_fields=['current_batch', 'updated_at'])
    return left


@transaction.atomic
def cancel_enrollment(enrollment, *, reason, user):
    enrollment = _lock(enrollment)
    if enrollment.status == Enrollment.Status.CANCELLED:
        raise ActionRefused('This enrollment is already cancelled.')
    old_status = enrollment.status
    enrollment.status = Enrollment.Status.CANCELLED
    enrollment.updated_by = user
    enrollment.save(update_fields=['status', 'updated_by', 'updated_at'])
    if enrollment.current_batch_id:
        _free_seat(enrollment, kind=Placement.Kind.LEAVE, reason=reason, user=user)
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


def mark_converted(lead):
    """Note when the lead became a student (only the first time)."""
    if lead.is_converted:
        return
    lead.is_converted = True
    lead.converted_at = timezone.now()
    lead.save(update_fields=['is_converted', 'converted_at', 'lead_score'])


def student_for_lead(lead, *, enrollment, user):
    """The lead's student, made from the lead's details the first time.

    Also marks the lead converted. Returns (student, created).
    """
    from apps.student.models import Student

    student = Student.objects.filter(lead=lead).order_by('id').first()
    created = student is None
    if created:
        student = Student.objects.create(
            full_name=lead.full_name,
            phone=lead.phone or '',
            secondary_phone=lead.secondary_phone,
            email=lead.email,
            address=lead.address,
            source=lead.source,
            branch=lead.branch or enrollment.branch,
            assigned_counselor=lead.assigned_counselor,
            lead=lead,
            enrolled_at=enrollment.enrollment_date,
            notes=(
                f'Became a student when the {enrollment.course.name} enrollment was '
                f'confirmed (lead {lead.reference_number}).'
            ),
            created_by=user,
        )
    mark_converted(lead)
    return student, created


def issue_fee_invoice(enrollment, *, user):
    """Make the course-fee invoice for an enrollment, unless it has an active one."""
    from apps.invoices.services import create_invoice

    if enrollment.invoices.filter(type='enrollment', status='active').exists():
        return None
    invoice, _ = create_invoice(
        enrollment=enrollment,
        invoice_type='enrollment',
        amount=enrollment.agreed_amount,
        created_by=user,
    )
    return invoice


@transaction.atomic
def confirm_enrollment(enrollment, *, user):
    """Confirm a pending enrollment: the moment a lead becomes a student.

    In one step: the enrollment is confirmed, the lead's student is found or
    made, the enrollment is linked to it, the lead is marked converted, a
    history row is written and the fee invoice is made. Payment does not
    decide this; a student can pay later or in parts.
    """
    enrollment = _lock(enrollment)
    if enrollment.status != Enrollment.Status.PENDING:
        raise ActionRefused('Only a pending enrollment can be confirmed.')
    lead = Lead._base_manager.select_for_update().get(pk=enrollment.lead_id)

    # Confirm takes a seat in the chosen batch. A full or closed batch stops
    # the confirm, and the whole step is undone.
    if enrollment.batch_id:
        _take_seat(
            enrollment, enrollment.batch,
            kind=Placement.Kind.JOIN,
            reason='Joined when the enrollment was confirmed.',
            user=user,
        )

    if enrollment.student_id:
        student, created = enrollment.student, False
        mark_converted(lead)
    else:
        student, created = student_for_lead(lead, enrollment=enrollment, user=user)

    enrollment.student = student
    enrollment.status = Enrollment.Status.CONFIRMED
    enrollment.updated_by = user
    enrollment.save(update_fields=['student', 'status', 'updated_by', 'updated_at'])
    EnrollmentHistory.record(
        enrollment=enrollment,
        action=EnrollmentHistory.Action.CONFIRM,
        reason=(
            'Enrollment confirmed. The lead is now a student.' if created
            else 'Enrollment confirmed.'
        ),
        performed_by=user,
        before={'status': Enrollment.Status.PENDING},
        after={
            'status': enrollment.status,
            'student': student.student_id,
            'batch': enrollment.current_batch.coursebatch_id if enrollment.current_batch else None,
        },
    )
    issue_fee_invoice(enrollment, user=user)
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
    # Its receipt no longer stands for real money, so it is cancelled too.
    from apps.invoices.services import cancel_invoice

    for receipt in payment.invoices.filter(status='active'):
        cancel_invoice(receipt, reason=f'Payment voided: {reason}', user=user)
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
