"""Batch money: fees, collected, owed, costs and profit.

Money counts on the money batch (Enrollment.batch), the batch the student
first joined. A student who moved in from another batch is counted there, not
here.

- students: confirmed, active and completed enrollments
- fees: what those students were charged (agreed + extra charges - discounts - write-offs)
- collected: payments less refunds, from every enrollment of the batch
  (money kept from a cancelled enrollment still counts)
- owed: what those students still owe
- costs: the batch's costs that are not voided
- profit: collected - costs
"""
from decimal import Decimal

from django.db.models import DecimalField, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce

from apps.course.models import BatchCost

ZERO = Decimal('0')
MONEY_FIELDS = ('fees', 'collected', 'owed', 'costs', 'profit')


def empty_figures():
    return {'students': 0, **{name: ZERO for name in MONEY_FIELDS}}


def _per_enrollment(queryset):
    """Sum of amount per enrollment, as a subquery for Enrollment.annotate()."""
    total = (
        queryset.filter(enrollment=OuterRef('pk'))
        .order_by()
        .values('enrollment')
        .annotate(total=Sum('amount'))
        .values('total')
    )
    return Coalesce(
        Subquery(total, output_field=DecimalField(max_digits=14, decimal_places=2)),
        Value(ZERO),
    )


def _with_money(enrollments):
    """Enrollments with what each paid, was charged extra, discounted, written off and refunded."""
    from apps.lead.models import Adjustment, Payment

    kinds = Adjustment.Kind
    return enrollments.annotate(
        paid=_per_enrollment(Payment.objects.filter(status=Payment.Status.SUCCESSFUL)),
        extra=_per_enrollment(Adjustment.objects.filter(kind=kinds.EXTRA_CHARGE)),
        discount=_per_enrollment(Adjustment.objects.filter(kind=kinds.DISCOUNT)),
        write_off=_per_enrollment(Adjustment.objects.filter(kind=kinds.WRITE_OFF)),
        refund=_per_enrollment(Adjustment.objects.filter(kind=kinds.REFUND)),
    )


def _fee(e):
    return e['agreed_amount'] + e['extra'] - e['discount'] - e['write_off']


def batch_owing(batch_id):
    """Students of the money batch who still owe, biggest amount first: (enrollment, owed)."""
    from apps.lead.models import Enrollment

    rows = []
    enrollments = _with_money(
        Enrollment.objects.filter(batch_id=batch_id, status__in=Enrollment.SEAT_STATUSES)
    ).select_related('lead', 'student')
    for e in enrollments:
        values = {name: getattr(e, name) for name in ('agreed_amount', 'extra', 'discount', 'write_off')}
        owed = _fee(values) - (e.paid - e.refund)
        if owed > 0:
            rows.append((e, owed))
    rows.sort(key=lambda r: (-r[1], r[0].id))
    return rows


def batch_figures(batch_ids):
    """{batch id: figures} counted from today's data."""
    from apps.lead.models import Enrollment

    batch_ids = list(batch_ids)
    figures = {pk: empty_figures() for pk in batch_ids}
    if not batch_ids:
        return figures

    enrollments = _with_money(Enrollment.objects.filter(batch_id__in=batch_ids)).values(
        'batch_id', 'status', 'agreed_amount', 'paid', 'extra', 'discount', 'write_off', 'refund',
    )
    for e in enrollments:
        f = figures[e['batch_id']]
        collected = e['paid'] - e['refund']
        f['collected'] += collected
        if e['status'] in Enrollment.SEAT_STATUSES:
            fee = _fee(e)
            f['students'] += 1
            f['fees'] += fee
            f['owed'] += max(fee - collected, ZERO)

    costs = (
        BatchCost.objects.filter(course_batch_id__in=batch_ids, voided_at__isnull=True)
        .values('course_batch_id')
        .annotate(total=Sum('amount'))
    )
    for row in costs:
        figures[row['course_batch_id']]['costs'] = row['total']

    for f in figures.values():
        f['profit'] = f['collected'] - f['costs']
    return figures


def figures_json(figures):
    """Money as strings, the way the other money APIs send it."""
    return {
        'students': figures['students'],
        **{name: str(figures[name]) for name in MONEY_FIELDS},
    }
