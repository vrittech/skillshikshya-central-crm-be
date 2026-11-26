"""Enrollment and money actions that must leave a reason trail.

Each action changes the data and writes an EnrollmentHistory row inside one
transaction, so a change can never be saved without its reason. Views only
check the request and call these functions.
"""
from decimal import Decimal

from django.db import transaction
from django.db.models import OuterRef, Q, Subquery, Sum
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
    if batch.branch_id and enrollment.branch_id and batch.branch_id != enrollment.branch_id:
        raise ActionRefused(
            f'{batch.name} is a batch of {batch.branch.name}. To study there, move the '
            'student to that branch first.'
        )
    refusal = batch.seat_refusal(exclude=enrollment)
    if refusal:
        raise ActionRefused(refusal)
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


# Enrollments that move with a student; finished and cancelled ones stay put.
MOVING_STATUSES = (Enrollment.Status.PENDING, Enrollment.Status.CONFIRMED, Enrollment.Status.ACTIVE)


@transaction.atomic
def move_student_branch(student, branch, *, reason, user):
    """Move a student, their open enrollments and their documents to another branch.

    A batch belongs to one branch, so a seated enrollment leaves its batch;
    join a batch of the new branch next. Payments, invoices and money entries
    stay with the branch that took them. Adds one BranchMove row, and a
    history row on each enrollment that moved.
    """
    from apps.student.models import BranchMove, Student, StudentDocument

    student = Student.objects.select_for_update(of=('self',)).select_related('branch').get(pk=student.pk)
    old = student.branch
    if old is not None and old.pk == branch.pk:
        raise ActionRefused(f'{student.full_name} is already at {branch.name}.')
    if not branch.is_active:
        raise ActionRefused(f'{branch.name} is not active.')
    reason = (reason or '').strip()
    if not reason:
        raise ActionRefused('Give a reason for the move.')
    note = f'Moved from {old.name if old else "no branch"} to {branch.name}: {reason}'

    enrollments = (
        Enrollment.objects.select_for_update(of=('self',))
        .select_related('current_batch')
        .filter(student=student, status__in=MOVING_STATUSES)
        .order_by('id')
    )
    for enrollment in enrollments:
        left = None
        if enrollment.current_batch_id:
            left = _free_seat(enrollment, kind=Placement.Kind.LEAVE, reason=note, user=user)
        enrollment.branch = branch
        enrollment.save(update_fields=['branch', 'updated_at'])
        EnrollmentHistory.record(
            enrollment=enrollment,
            action=EnrollmentHistory.Action.MOVE_BRANCH,
            reason=note,
            performed_by=user,
            before={'branch': old.name if old else None, 'batch': _code(left) if left else None},
            after={'branch': branch.name, 'batch': None},
        )

    StudentDocument.objects.filter(student=student).update(branch=branch)
    student.branch = branch
    student.updated_by = user
    student.save(update_fields=['branch', 'updated_by', 'updated_at'])
    BranchMove.objects.create(
        student=student, from_branch=old, to_branch=branch, reason=reason, moved_by=user,
    )
    return student


def latest_placement(field):
    """The newest seat-history value of each enrollment, for .annotate()."""
    return Subquery(
        Placement.objects.filter(enrollment=OuterRef('pk'))
        .order_by('-created_at', '-id').values(field)[:1]
    )


def with_seat(queryset):
    """Adds seat_kind, seat_on and seat_from_name from each enrollment's newest seat-history row."""
    return queryset.annotate(
        seat_kind=latest_placement('kind'),
        seat_on=latest_placement('started_on'),
        seat_from_name=latest_placement('from_batch__name'),
    )


SEAT_STATES = ('seated', 'paused', 'none')


def filter_seat(queryset, state):
    """Keep 'seated', 'paused' or 'none' enrollments. The queryset must come from with_seat()."""
    if state == 'seated':
        return queryset.filter(current_batch__isnull=False)
    queryset = queryset.filter(current_batch__isnull=True)
    if state == 'paused':
        return queryset.filter(seat_kind=Placement.Kind.PAUSE)
    return queryset.filter(Q(seat_kind__isnull=True) | ~Q(seat_kind=Placement.Kind.PAUSE))


def annotated_seat_state(enrollment):
    """seat_state() for an enrollment from with_seat(), with no extra query."""
    if enrollment.current_batch_id:
        return 'seated'
    return 'paused' if enrollment.seat_kind == Placement.Kind.PAUSE else 'none'


def batch_students(batch):
    """Who sits in a batch now, who is paused out of it, and who moved out of it.

    Each row says how the student got there. A student whose money batch is
    another batch moved in, so this batch's revenue leaves them out. A student
    who moved out keeps their money on their money batch.
    """
    enrollments = with_seat(
        Enrollment.objects.filter(status__in=Enrollment.SEAT_STATUSES)
    ).annotate(
        last_from=latest_placement('from_batch'),
    ).select_related('lead', 'student', 'batch', 'current_batch').order_by('lead__full_name', 'id')

    seated = list(enrollments.filter(current_batch=batch))
    paused = list(enrollments.filter(
        current_batch__isnull=True, seat_kind=Placement.Kind.PAUSE, last_from=batch.pk,
    ))
    moves_out = Placement.objects.filter(from_batch=batch, kind=Placement.Kind.MOVE)
    last_move_out = moves_out.filter(enrollment=OuterRef('pk')).order_by('-created_at', '-id')
    paused_ids = {e.id for e in paused}
    moved_out = [
        e for e in enrollments.filter(pk__in=moves_out.values('enrollment')).exclude(
            current_batch=batch,
        ).annotate(
            out_on=Subquery(last_move_out.values('started_on')[:1]),
            out_to=Subquery(last_move_out.values('course_batch')[:1]),
        )
        if e.id not in paused_ids
    ]
    names = dict(
        CourseBatch.objects.filter(
            pk__in={e.last_from for e in seated if e.last_from} | {e.out_to for e in moved_out}
        ).values_list('pk', 'name')
    )
    kinds = dict(Placement.Kind.choices)

    def row(e, *, paused_row=False):
        how = e.seat_kind
        from_id = None if paused_row else e.last_from
        return {
            'enrollment': e.id,
            'lead': e.lead_id,
            'name': e.student.full_name if e.student_id else e.lead.full_name,
            'phone': e.lead.phone,
            'student_id': e.student.student_id if e.student_id else None,
            'status': e.status,
            'since': e.seat_on,
            'how': how,
            'how_display': kinds.get(how, ''),
            'from_batch': from_id,
            'from_batch_name': names.get(from_id),
            'money_batch': e.batch_id,
            'money_batch_name': e.batch.name if e.batch_id else None,
            'moved_in': e.batch_id is not None and e.batch_id != batch.pk,
        }

    def moved_out_row(e):
        return {
            **row(e),
            'since': e.out_on,
            'how': Placement.Kind.MOVE,
            'how_display': kinds[Placement.Kind.MOVE],
            'from_batch': batch.pk,
            'from_batch_name': batch.name,
            'moved_in': False,
            'to_batch': e.out_to,
            'to_batch_name': names.get(e.out_to),
            # Where they are now: the batch they moved to, a later batch, paused or gone.
            'seat_state': annotated_seat_state(e),
            'now_batch': e.current_batch_id,
            'now_batch_name': e.current_batch.name if e.current_batch_id else None,
        }

    return {
        'seated': [row(e) for e in seated],
        'paused': [row(e, paused_row=True) for e in paused],
        'moved_out': [moved_out_row(e) for e in moved_out],
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
    # The Teams licence stays with the student and shows "Action required".
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


# Statuses Change status can set. Confirm, Cancel and Reinstate have their own actions.
STATUS_CHOICES = (
    Enrollment.Status.CONFIRMED, Enrollment.Status.ACTIVE, Enrollment.Status.COMPLETED,
    Enrollment.Status.DROPPED_OUT, Enrollment.Status.TRANSFERRED,
)
LEAVING_STATUSES = (Enrollment.Status.DROPPED_OUT, Enrollment.Status.TRANSFERRED)


@transaction.atomic
def change_status(enrollment, status, *, reason, user):
    """Set a confirmed enrollment's status, with a reason.

    Dropping out or transferring frees the seat. Coming back from those
    leaves the student without a seat until Join batch.
    """
    enrollment = _lock(enrollment)
    old_status = enrollment.status
    if old_status == Enrollment.Status.PENDING:
        raise ActionRefused('Confirm the enrollment first.')
    if old_status == Enrollment.Status.CANCELLED:
        raise ActionRefused('This enrollment is cancelled. Use Reinstate to reopen it.')
    if status not in STATUS_CHOICES:
        raise ActionRefused('Pick another status.')
    if status == old_status:
        raise ActionRefused(f'This enrollment is already {enrollment.get_status_display().lower()}.')
    enrollment.status = status
    enrollment.updated_by = user
    enrollment.save(update_fields=['status', 'updated_by', 'updated_at'])
    if status in LEAVING_STATUSES and enrollment.current_batch_id:
        _free_seat(enrollment, kind=Placement.Kind.LEAVE, reason=reason, user=user)
    EnrollmentHistory.record(
        enrollment=enrollment,
        action=EnrollmentHistory.Action.CHANGE_STATUS,
        reason=reason,
        performed_by=user,
        before={'status': old_status},
        after={'status': status},
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
            # The student belongs where they enrolled, which may not be
            # where they first asked.
            branch=enrollment.branch or lead.branch,
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


def issue_fee_invoice(enrollment, *, user, email=True):
    """Make the course-fee invoice for an enrollment, unless it has an active one.

    With email=True the invoice is emailed on its own. Confirm passes False and
    sends it with the confirmation email instead.
    """
    from apps.invoices.services import create_invoice
    from apps.lead.emails import invoice_again

    if enrollment.invoices.filter(type='enrollment', status='active').exists():
        return None
    invoice = create_invoice(
        enrollment=enrollment,
        invoice_type='enrollment',
        amount=enrollment.agreed_amount,
        created_by=user,
    )
    if email:
        invoice_again(invoice, user=user)
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


def net_paid(enrollment):
    """Successful payments less refunds."""
    return Decimal(enrollment.total_paid) - enrollment.refunds_total


def _npr(amount):
    from apps.lead.emails import npr

    return npr(amount)


def payment_refusal(enrollment, amount, *, pending_too=True, leave_out=None):
    """Why a payment of `amount` can't be taken, or None.

    A payment can't be more than what is left to pay, so a typo (100000 for
    10000) is caught before a receipt goes to the student. With pending_too,
    payments still pending count as on their way; leave_out is a pending
    payment being confirmed now.
    """
    left = Decimal(enrollment.remaining_amount)
    if pending_too:
        pending = enrollment.payments.filter(status=Payment.Status.PENDING)
        if leave_out is not None:
            pending = pending.exclude(pk=leave_out.pk)
        left -= pending.aggregate(total=Sum('amount'))['total'] or 0
    if left <= 0:
        return 'Nothing is left to pay on this enrollment.'
    if Decimal(amount) > left:
        return f'This is more than what is left to pay ({_npr(left)}). Check the amount.'
    return None


@transaction.atomic
def confirm_enrollment(enrollment, *, user, due_dates=None, first_payment=None):
    """Confirm a pending enrollment: the moment a lead becomes a student.

    In one step: the enrollment is confirmed, the lead's student is found or
    made, the enrollment is linked to it, the lead is marked converted, a
    history row is written and the fee invoice is made.

    Something must be paid first (unless the agreed amount is 0); the rest
    can be paid later or in parts. first_payment ({'amount', 'payment_method',
    'payment_date', 'transaction_reference', 'notes'}) records that payment in
    the same step, so a refused confirm keeps no payment either.

    due_dates is a list of {'due_on', 'amount'} that adds up to the agreed
    amount. Without it, the whole amount is due on the confirm day.
    """
    enrollment = _lock(enrollment)
    if enrollment.status != Enrollment.Status.PENDING:
        raise ActionRefused('Only a pending enrollment can be confirmed.')
    payment = None
    if first_payment:
        refusal = payment_refusal(enrollment, first_payment['amount'])
        if refusal:
            raise ActionRefused(refusal)
        payment = Payment.objects.create(
            enrollment=enrollment, status=Payment.Status.SUCCESSFUL, received_by=user,
            branch=enrollment.branch,
            payment_date=first_payment.get('payment_date') or timezone.now(),
            amount=first_payment['amount'],
            payment_method=first_payment['payment_method'],
            transaction_reference=first_payment.get('transaction_reference') or None,
            notes=first_payment.get('notes') or None,
        )
    if enrollment.agreed_amount > 0 and net_paid(enrollment) <= 0:
        raise ActionRefused('Record a payment first. An enrollment is confirmed once something is paid.')
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
    from apps.lead.emails import enrollment_confirmed

    invoice = issue_fee_invoice(enrollment, user=user, email=False)
    enrollment_confirmed(enrollment, invoice, user=user)
    if payment is not None:
        issue_receipt(payment, user=user)
    return enrollment


def issue_receipt(payment, *, user):
    """Make the receipt for a successful payment, once, and email it to the student."""
    from apps.invoices.services import create_invoice
    from apps.lead.emails import payment_received

    if payment.status != Payment.Status.SUCCESSFUL or payment.invoices.exists():
        return None
    invoice = create_invoice(
        enrollment=payment.enrollment,
        invoice_type='payment',
        amount=payment.amount,
        payment=payment,
        created_by=user,
    )
    payment_received(payment, invoice, user=user)
    return invoice


@transaction.atomic
def confirm_payment(payment, *, user):
    """Pending -> successful (the money arrived). Issues the receipt."""
    payment = _lock(payment)
    if payment.status != Payment.Status.PENDING:
        raise ActionRefused('Only a pending payment can be confirmed.')
    refusal = payment_refusal(_lock(payment.enrollment), payment.amount, leave_out=payment)
    if refusal:
        raise ActionRefused(refusal)
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
    can't be more than what is owed. A discount can't be more than the fee
    (after earlier discounts and write-offs); when the student already paid,
    it may leave a credit we owe them, to be refunded.
    """
    enrollment = _lock(enrollment)
    balance_before = enrollment.remaining_amount
    if kind == Adjustment.Kind.DISCOUNT:
        t = enrollment.adjustment_totals()
        fee = (
            enrollment.agreed_amount + t[Adjustment.Kind.EXTRA_CHARGE]
            - t[Adjustment.Kind.DISCOUNT] - t[Adjustment.Kind.WRITE_OFF]
        )
        if amount > fee:
            raise ActionRefused(
                f"A discount can't be more than the fee ({_npr(max(fee, 0))} "
                'after earlier discounts and write-offs).'
            )
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
