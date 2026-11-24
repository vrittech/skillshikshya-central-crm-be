"""Enrollment and money actions that must leave a reason trail.

Each action changes the data and writes an EnrollmentHistory row inside one
transaction, so a change can never be saved without its reason. Views only
check the request and call these functions.
"""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException

from apps.course.models import CourseBatch
from apps.lead.models import (
    Adjustment, Enrollment, EnrollmentHistory, Installment, Lead, Payment, PaymentPlan, Placement,
)


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


def _last_placement(enrollment):
    return enrollment.placements.order_by('-created_at', '-id').first()


def seat_state(enrollment):
    """'seated' (in a batch now), 'paused', or 'none' (never placed, or left)."""
    if enrollment.current_batch_id:
        return 'seated'
    last = _last_placement(enrollment)
    return 'paused' if last is not None and last.kind == Placement.Kind.PAUSE else 'none'


def _must_be_able_to_sit(enrollment):
    if enrollment.status == Enrollment.Status.PENDING:
        raise ActionRefused('Confirm the enrollment first. A pending enrollment has no seat.')
    if enrollment.status not in Enrollment.SEAT_STATUSES:
        raise ActionRefused(
            f'This enrollment is {enrollment.get_status_display().lower()}, '
            'so it cannot sit in a batch.'
        )


def _code(batch):
    return batch.coursebatch_id if batch is not None else None


def _free_teams_licence(enrollment, *, reason, user):
    from apps.lead.teams import free_for_enrollment

    free_for_enrollment(enrollment, reason=reason, user=user)


def _pause_teams_licence(enrollment, *, reason, user):
    from apps.lead.teams import pause_for_enrollment

    pause_for_enrollment(enrollment, reason=reason, user=user)


def _resume_teams_licence(enrollment, *, reason, user):
    from apps.lead.teams import resume_for_enrollment

    resume_for_enrollment(enrollment, reason=reason, user=user)


@transaction.atomic
def join_batch(enrollment, batch, *, user, reason='', on=None):
    """Seat a confirmed enrollment that sits nowhere (confirmed without a batch, or reinstated)."""
    enrollment = _lock(enrollment)
    _must_be_able_to_sit(enrollment)
    state = seat_state(enrollment)
    if state == 'seated':
        raise ActionRefused(
            f'This student already sits in {enrollment.current_batch.name}. '
            'Use Move to change the batch.'
        )
    if state == 'paused':
        raise ActionRefused('This student is paused. Use Resume to bring them back.')
    reason = (reason or '').strip() or 'Joined the batch.'
    batch = _take_seat(enrollment, batch, kind=Placement.Kind.JOIN, reason=reason, user=user, on=on)
    EnrollmentHistory.record(
        enrollment=enrollment,
        action=EnrollmentHistory.Action.JOIN_BATCH,
        reason=reason,
        performed_by=user,
        before={'batch': None},
        after={'batch': _code(batch)},
    )
    return enrollment


@transaction.atomic
def move_batch(enrollment, batch, *, reason, user, on=None):
    """Move a seated student to another batch of the same course.

    The money stays on the money batch (enrollment.batch). Only the seat moves.
    """
    enrollment = _lock(enrollment)
    _must_be_able_to_sit(enrollment)
    old = enrollment.current_batch
    if old is None:
        raise ActionRefused(
            'This student is not in a batch now. Use Join batch, or Resume if they are paused.'
        )
    if old.pk == batch.pk:
        raise ActionRefused(f'This student already sits in {old.name}.')
    batch = _take_seat(
        enrollment, batch, kind=Placement.Kind.MOVE, reason=reason, user=user, on=on,
        from_batch=old,
    )
    EnrollmentHistory.record(
        enrollment=enrollment,
        action=EnrollmentHistory.Action.SWITCH_BATCH,
        reason=reason,
        performed_by=user,
        before={'batch': _code(old)},
        after={'batch': _code(batch)},
    )
    return enrollment


@transaction.atomic
def pause_enrollment(enrollment, *, reason, user, on=None):
    """Take a seated student out of their batch for now. The seat is freed; a
    Teams licence is paused, not freed."""
    enrollment = _lock(enrollment)
    _must_be_able_to_sit(enrollment)
    if enrollment.current_batch_id is None:
        raise ActionRefused('This student is not in a batch now, so there is nothing to pause.')
    left = _free_seat(enrollment, kind=Placement.Kind.PAUSE, reason=reason, user=user, on=on)
    EnrollmentHistory.record(
        enrollment=enrollment,
        action=EnrollmentHistory.Action.PAUSE,
        reason=reason,
        performed_by=user,
        before={'batch': _code(left)},
        after={'batch': None},
    )
    _pause_teams_licence(enrollment, reason=f'Paused: {reason}', user=user)
    return enrollment


@transaction.atomic
def resume_enrollment(enrollment, batch, *, reason, user, on=None):
    """Bring a paused student back, into the batch they left or another of the same course."""
    enrollment = _lock(enrollment)
    _must_be_able_to_sit(enrollment)
    last = _last_placement(enrollment)
    if enrollment.current_batch_id or last is None or last.kind != Placement.Kind.PAUSE:
        raise ActionRefused('Only a paused student can resume.')
    batch = _take_seat(
        enrollment, batch, kind=Placement.Kind.RESUME, reason=reason, user=user, on=on,
        from_batch=last.from_batch,
    )
    EnrollmentHistory.record(
        enrollment=enrollment,
        action=EnrollmentHistory.Action.RESUME,
        reason=reason,
        performed_by=user,
        # The batch they paused from is on the seat history row (from_batch).
        before={'batch': None},
        after={'batch': _code(batch)},
    )
    _resume_teams_licence(enrollment, reason=f'Resumed: {reason}', user=user)
    return enrollment


def batch_students(batch):
    """Who sits in a batch now, and who is paused out of it.

    Each row says how the student got there. A student whose money batch is
    another batch moved in, so this batch's revenue leaves them out.
    """
    from django.db.models import OuterRef, Subquery

    latest = Placement.objects.filter(enrollment=OuterRef('pk')).order_by('-created_at', '-id')
    enrollments = Enrollment.objects.filter(status__in=Enrollment.SEAT_STATUSES).annotate(
        last_kind=Subquery(latest.values('kind')[:1]),
        last_from=Subquery(latest.values('from_batch')[:1]),
        last_on=Subquery(latest.values('started_on')[:1]),
    ).select_related('lead', 'student', 'batch').order_by('lead__full_name', 'id')

    seated = list(enrollments.filter(current_batch=batch))
    paused = list(enrollments.filter(
        current_batch__isnull=True, last_kind=Placement.Kind.PAUSE, last_from=batch.pk,
    ))
    names = dict(
        CourseBatch.objects.filter(
            pk__in={e.last_from for e in seated if e.last_from}
        ).values_list('pk', 'name')
    )
    kinds = dict(Placement.Kind.choices)

    def row(e, *, paused_row=False):
        how = e.last_kind
        from_id = None if paused_row else e.last_from
        return {
            'enrollment': e.id,
            'lead': e.lead_id,
            'name': e.student.full_name if e.student_id else e.lead.full_name,
            'phone': e.lead.phone,
            'student_id': e.student.student_id if e.student_id else None,
            'status': e.status,
            'since': e.last_on,
            'how': how,
            'how_display': kinds.get(how, ''),
            'from_batch': from_id,
            'from_batch_name': names.get(from_id),
            'money_batch': e.batch_id,
            'money_batch_name': e.batch.name if e.batch_id else None,
            'moved_in': e.batch_id is not None and e.batch_id != batch.pk,
        }

    return {
        'seated': [row(e) for e in seated],
        'paused': [row(e, paused_row=True) for e in paused],
    }


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
    _free_teams_licence(enrollment, reason=f'Cancelled: {reason}', user=user)
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
def reissue_fee_invoice(enrollment, *, user):
    """Make a new fee invoice after the old one was cancelled."""
    enrollment = _lock(enrollment)
    if enrollment.status not in Enrollment.SEAT_STATUSES:
        raise ActionRefused('A fee invoice is made only for a confirmed enrollment.')
    invoice = issue_fee_invoice(enrollment, user=user)
    if invoice is None:
        raise ActionRefused('This enrollment already has an active fee invoice.')
    return invoice


@transaction.atomic
def confirm_enrollment(enrollment, *, user, due_dates=None):
    """Confirm a pending enrollment: the moment a lead becomes a student.

    In one step: the enrollment is confirmed, the lead's student is found or
    made, the enrollment is linked to it, the lead is marked converted, a
    history row is written and the fee invoice is made. Payment does not
    decide this; a student can pay later or in parts.

    due_dates is a list of {'due_on', 'amount'} that adds up to the agreed
    amount. Without it, the whole amount is due on the confirm day.
    """
    enrollment = _lock(enrollment)
    if enrollment.status != Enrollment.Status.PENDING:
        raise ActionRefused('Only a pending enrollment can be confirmed.')
    if due_dates:
        due_dates = _clean_parts(enrollment, due_dates)
    elif enrollment.agreed_amount > 0:
        due_dates = [{'due_on': timezone.localdate(), 'amount': enrollment.agreed_amount}]
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
    from apps.lead.enquiries import win_for_enrollment

    win_for_enrollment(enrollment, user=user)
    if due_dates:
        _make_plan(
            enrollment, due_dates, reason='Set when the enrollment was confirmed.', user=user,
        )
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
            'due_dates': _parts_json(due_dates or []),
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


# ---------------------------------------------------------------- due dates


def current_plan(enrollment):
    """The newest payment plan, or None. Uses the prefetch cache when the view loaded it."""
    plans = list(enrollment.payment_plans.all())  # newest first
    return plans[0] if plans else None


def _clean_parts(enrollment, parts):
    """Due dates must add up to the agreed amount. Returns them oldest first."""
    if not parts:
        raise ActionRefused('Add at least one due date.')
    total = sum((Decimal(p['amount']) for p in parts), Decimal('0'))
    if total != enrollment.agreed_amount:
        raise ActionRefused(
            f'The due dates add up to {total}, but the agreed amount is '
            f'{enrollment.agreed_amount}.'
        )
    return sorted(parts, key=lambda p: p['due_on'])


def _parts_json(parts):
    """Due dates as plain values for a history row."""
    def get(part, key):
        return part[key] if isinstance(part, dict) else getattr(part, key)

    return [
        {'due_on': get(p, 'due_on').isoformat(), 'amount': str(get(p, 'amount'))}
        for p in parts
    ]


def _make_plan(enrollment, parts, *, reason, user):
    plan = PaymentPlan.objects.create(
        enrollment=enrollment, reason=reason, branch=enrollment.branch, created_by=user,
    )
    for part in parts:
        Installment.objects.create(plan=plan, due_on=part['due_on'], amount=part['amount'])
    return plan


@transaction.atomic
def set_due_dates(enrollment, parts, *, reason, user):
    """Change the due dates, with a reason. The old plan is kept; the new one counts."""
    enrollment = _lock(enrollment)
    if enrollment.status not in Enrollment.SEAT_STATUSES:
        raise ActionRefused('Due dates can be set only on a confirmed enrollment.')
    parts = _clean_parts(enrollment, parts)
    old = current_plan(enrollment)
    _make_plan(enrollment, parts, reason=reason, user=user)
    EnrollmentHistory.record(
        enrollment=enrollment,
        action=EnrollmentHistory.Action.CHANGE_DUE_DATES,
        reason=reason,
        performed_by=user,
        before={'due_dates': _parts_json(old.installments.all()) if old else []},
        after={'due_dates': _parts_json(parts)},
    )
    return enrollment


def due_rows(enrollment, *, today=None, paid=None):
    """What the student has to pay, oldest due date first, and how much of each is paid.

    The rows are the parts of the newest payment plan, plus each extra charge,
    due on the day it was added. With no plan, the agreed amount is one row
    with no due date. Payments (less refunds), discounts and write-offs fill
    the oldest row first.

    Each row: kind ('part' or 'extra_charge'), due_on, amount, paid, left,
    status ('paid', 'part_paid', 'due' or 'late') and days_late. Pass paid
    when the caller has already summed the successful payments.
    """
    today = today or timezone.localdate()
    totals = enrollment.adjustment_totals()
    plan = current_plan(enrollment)

    rows = []
    if plan:
        rows += [
            {'kind': 'part', 'due_on': part.due_on, 'amount': part.amount}
            for part in plan.installments.all()
        ]
    elif enrollment.agreed_amount > 0:
        rows.append({'kind': 'part', 'due_on': None, 'amount': enrollment.agreed_amount})
    rows += [
        {'kind': 'extra_charge', 'due_on': timezone.localdate(a.created_at), 'amount': a.amount}
        for a in enrollment.adjustments.all()
        if a.kind == Adjustment.Kind.EXTRA_CHARGE
    ]
    # Oldest first; a row with no due date goes last.
    rows.sort(key=lambda r: (r['due_on'] is None, r['due_on'] or today))

    paid = enrollment.total_paid if paid is None else paid
    credit = max(
        Decimal(paid)
        - totals[Adjustment.Kind.REFUND]
        + totals[Adjustment.Kind.DISCOUNT]
        + totals[Adjustment.Kind.WRITE_OFF],
        Decimal('0'),
    )
    for row in rows:
        filled = min(row['amount'], credit)
        credit -= filled
        left = row['amount'] - filled
        late = left > 0 and row['due_on'] is not None and row['due_on'] < today
        if left <= 0:
            status = 'paid'
        elif late:
            status = 'late'
        elif filled > 0:
            status = 'part_paid'
        else:
            status = 'due'
        row.update(
            paid=filled,
            left=left,
            status=status,
            days_late=(today - row['due_on']).days if late else 0,
        )
    return rows
