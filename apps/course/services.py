"""Batch actions that must leave a reason trail: costs, and (step 5) closing."""
from django.db import transaction
from django.utils import timezone

from apps.course.models import BatchCost
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
