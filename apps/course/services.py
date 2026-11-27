"""Batch actions that must leave a reason trail: costs, closing and reopening."""
from django.db import transaction
from django.utils import timezone

from apps.course import closing
from apps.course.models import BatchClosing, BatchCost, CourseBatch
from apps.course.money import MONEY_FIELDS, batch_figures
from apps.lead.services import ActionRefused


@transaction.atomic
def add_cost(batch, *, kind, amount, spent_on, user, note=''):
    return BatchCost.objects.create(
        course_batch=batch,
        kind=kind,
        amount=amount,
        spent_on=spent_on,
        note=note,
        branch=batch.branch,
        created_by=user,
    )


@transaction.atomic
def void_cost(cost, *, reason, user):
    """A wrong cost is voided, never edited or deleted. Add a new one if needed."""
    cost = BatchCost.objects.select_for_update().get(pk=cost.pk)
    if cost.voided_at is not None:
        raise ActionRefused('This cost is already voided.')
    cost.voided_at = timezone.now()
    cost.voided_by = user
    cost.void_reason = reason
    cost.save(update_fields=['voided_at', 'voided_by', 'void_reason'])
    return cost


# ---------------------------------------------------------------- close


def _lock(batch):
    return CourseBatch.objects.select_for_update().get(pk=batch.pk)


@transaction.atomic
def close_batch(batch, *, reason, user, close_anyway=False):
    """Close the batch, mark its students completed, and keep its figures as they are today.

    The close checklist (apps.course.closing) must be clear, or close_anyway
    set; what was still open is kept on the close row. Students sitting in the
    batch are marked completed; paused students and those who moved to
    another batch are not in it and stay as they are. Money and costs that
    come later still count on the batch; they show as "after close".
    """
    batch = _lock(batch)
    if batch.status == CourseBatch.Status.CLOSED:
        raise ActionRefused('This batch is already closed.')
    check = closing.close_check(batch)
    if not check['ready'] and not close_anyway:
        raise closing.CloseBlocked(check)
    completed = closing.complete_seated(batch, reason=f'Batch closed: {reason}', user=user)
    figures = batch_figures([batch.pk])[batch.pk]
    BatchClosing.objects.create(
        course_batch=batch, kind=BatchClosing.Kind.CLOSE, reason=reason, created_by=user,
        checklist={**closing.check_record(check, close_anyway=close_anyway), 'completed': completed},
        **figures,
    )
    batch.status = CourseBatch.Status.CLOSED
    batch.save(update_fields=['status', 'updated_at'])
    return batch


@transaction.atomic
def reopen_batch(batch, *, reason, user):
    """Open a closed batch again. It is running, or upcoming if it hasn't started.

    Students the close marked completed go back to their status before it.
    """
    batch = _lock(batch)
    if batch.status != CourseBatch.Status.CLOSED:
        raise ActionRefused('Only a closed batch can be reopened.')
    closed = last_close(batch)
    if closed is not None and closed.checklist:
        closing.undo_completed(
            batch, closed.checklist.get('completed', []), reason=f'Batch reopened: {reason}', user=user,
        )
    not_started = batch.start_date is not None and batch.start_date > timezone.localdate()
    batch.status = CourseBatch.Status.UPCOMING if not_started else CourseBatch.Status.RUNNING
    batch.save(update_fields=['status', 'updated_at'])
    BatchClosing.objects.create(
        course_batch=batch, kind=BatchClosing.Kind.REOPEN, reason=reason, created_by=user,
    )
    return batch


def last_close(batch):
    """The close row whose figures count now, or None if the batch is not closed."""
    if batch.status != CourseBatch.Status.CLOSED:
        return None
    last = batch.closings.order_by('-created_at', '-id').first()
    return last if last is not None and last.kind == BatchClosing.Kind.CLOSE else None


def close_figures(closing):
    return {
        'students': closing.students,
        **{name: getattr(closing, name) for name in MONEY_FIELDS},
    }


def after_close(now, at_close):
    """What changed since the close: money and costs that came later."""
    return {
        'students': now['students'] - at_close['students'],
        **{name: now[name] - at_close[name] for name in MONEY_FIELDS},
    }
