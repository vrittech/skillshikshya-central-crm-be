from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import Count, F, Prefetch, Q, Sum
from django.db.models.functions import Coalesce, TruncDate, TruncMonth, TruncWeek
from django.utils import timezone

from apps.lead.models import (
    Enrollment, FollowUp, Lead, LeadActivity, LeadCall,
    LeadCourseInterest, Payment, PaymentPlan,
)
from apps.lead.services import due_rows
from apps.callminutes.models import CallMinutesTransaction
from apps.course.models import CourseBatch
from apps.course.money import MONEY_FIELDS, batch_figures, empty_figures, figures_json
from apps.student.models import Student

User = get_user_model()

DATE_PARSE_ERROR = 'Invalid date. Use YYYY-MM-DD format.'

LEAD_FILTER_PARAMS = [
    'status', 'source', 'campaign', 'lead_type', 'priority',
    'assigned_counselor', 'primary_course', 'is_converted', 'is_archived',
]


def _to_bool(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return str(value).lower() in ('true', '1', 'yes')


def _query_params(request):
    if hasattr(request, 'query_params'):
        return request.query_params
    return request.GET


def _query_param(request, name, default=None):
    params = _query_params(request)
    return params.get(name, default)


def _resolve_date_range(request):
    start = _query_param(request, 'start_date')
    end = _query_param(request, 'end_date')
    start_date = None
    end_date = None
    try:
        if start:
            start_date = timezone.datetime.strptime(start, '%Y-%m-%d').date()
        if end:
            end_date = timezone.datetime.strptime(end, '%Y-%m-%d').date()
    except ValueError:
        return None, None, DATE_PARSE_ERROR
    if start_date and end_date and start_date > end_date:
        return None, None, 'start_date cannot be after end_date.'
    return start_date, end_date, None


def _branch_scope(request):
    user = getattr(request, 'user', None)
    if user is None:
        return None
    if user.is_staff or user.is_superuser:
        branch_id = _query_param(request, 'branch_id')
        return int(branch_id) if branch_id else None
    return getattr(user, 'branch_id', None)


def _scoped_leads(request):
    branch_id = _branch_scope(request)
    qs = Lead.objects.filter(is_deleted=False)
    if branch_id:
        qs = qs.filter(branch_id=branch_id)
    return qs, branch_id


def _apply_lead_filters(request, qs, date_field='created_at'):
    start_date, end_date, error = _resolve_date_range(request)
    if error:
        return qs, start_date, end_date, error
    if start_date:
        qs = qs.filter(**{f'{date_field}__date__gte': start_date})
    if end_date:
        qs = qs.filter(**{f'{date_field}__date__lte': end_date})
    for field in LEAD_FILTER_PARAMS:
        value = _query_param(request, field)
        if value in (None, ''):
            continue
        if field == 'is_converted':
            converted = _to_bool(value)
            if converted is not None:
                qs = qs.filter(is_converted=converted)
        elif field == 'is_archived':
            archived = _to_bool(value)
            if archived is not None:
                qs = qs.filter(is_archived=archived)
        else:
            qs = qs.filter(**{field: value})
    return qs, start_date, end_date, None


def _lead_dimension_breakdown(request, qs, dimension, dimension_label):
    start_date, end_date, error = None, None, None
    qs, start_date, end_date, error = _apply_lead_filters(request, qs)
    if error:
        return {'error': error}
    if dimension in ('status',):
        rows = (
            qs.values(dimension_label, 'status__name', 'status__color')
            .annotate(count=Count('id'))
            .order_by('-count')
        )
    elif dimension == 'source':
        rows = (
            qs.values(dimension_label, 'source__name')
            .annotate(count=Count('id'))
            .order_by('-count')
        )
    elif dimension == 'campaign':
        rows = (
            qs.values(dimension_label, 'campaign__name')
            .annotate(count=Count('id'))
            .order_by('-count')
        )
    elif dimension == 'lead_type':
        rows = (
            qs.values(dimension_label, 'lead_type__name')
            .annotate(count=Count('id'))
            .order_by('-count')
        )
    elif dimension == 'priority':
        rows = (
            qs.values(dimension_label, 'priority__name')
            .annotate(count=Count('id'))
            .order_by('-count')
        )
    elif dimension == 'assigned_counselor':
        rows = (
            qs.values(dimension_label, 'assigned_counselor__name')
            .annotate(count=Count('id'))
            .order_by('-count')
        )
    elif dimension == 'branch':
        rows = (
            qs.values(dimension_label, 'branch__name')
            .annotate(count=Count('id'))
            .order_by('-count')
        )
    else:
        rows = (
            qs.values(dimension_label)
            .annotate(count=Count('id'))
            .order_by('-count')
        )
    return {
        'dimension': dimension,
        'total': qs.count(),
        'date_range': {
            'start_date': start_date.isoformat() if start_date else None,
            'end_date': end_date.isoformat() if end_date else None,
        },
        'results': list(rows),
    }


def lead_overview_report(request):
    qs, branch_id = _scoped_leads(request)
    qs, start_date, end_date, error = _apply_lead_filters(request, qs)
    if error:
        return {'error': error}

    total = qs.count()
    converted = qs.filter(is_converted=True).count()
    conversion_rate = round((converted / total * 100), 1) if total else 0

    archived = qs.filter(is_archived=True).count()

    scope_qs = qs
    if start_date:
        scope_qs = scope_qs.filter(created_at__date__gte=start_date)
    if end_date:
        scope_qs = scope_qs.filter(created_at__date__lte=end_date)
    new_count = scope_qs.count()
    new_converted = scope_qs.filter(is_converted=True).count()

    followups = FollowUp.objects.filter(branch_id=branch_id) if branch_id else FollowUp.objects.all()
    followup_qs = followups.filter(lead__is_deleted=False)
    if start_date:
        followup_qs = followup_qs.filter(scheduled_at__date__gte=start_date)
    if end_date:
        followup_qs = followup_qs.filter(scheduled_at__date__lte=end_date)

    clead_ids = qs.values_list('id', flat=True)
    range_ids = scope_qs.values_list('id', flat=True)

    enrollments = Enrollment.objects.filter(lead_id__in=range_ids)
    if branch_id:
        enrollments = enrollments.filter(branch_id=branch_id)
    revenue_collected = (
        Payment.objects.filter(
            status='successful',
            enrollment__lead_id__in=range_ids,
        ).filter(branch_id=branch_id) if branch_id else
        Payment.objects.filter(status='successful', enrollment__lead_id__in=range_ids)
    ).aggregate(total=Coalesce(Sum('amount'), Decimal('0')))['total']

    expected_revenue = (
        qs.aggregate(total=Coalesce(Sum('expected_revenue'), Decimal('0')))['total']
    )

    loses = qs.filter(status__is_final=True).count()

    return {
        'date_range': {
            'start_date': start_date.isoformat() if start_date else None,
            'end_date': end_date.isoformat() if end_date else None,
        },
        'leads': {
            'total': total,
            'new_in_range': new_count,
            'converted_total': converted,
            'converted_in_range': new_converted,
            'conversion_rate': conversion_rate,
            'archived': archived,
            'lost': loses,
        },
        'pipeline': {
            'active_leads': qs.filter(is_archived=False).count(),
            'expected_revenue': str(expected_revenue),
        },
        'enrollments': {
            'total': enrollments.count(),
            'revenue_collected': str(revenue_collected),
        },
        'students': Student.objects.count(),
        'communication': {
            'calls': LeadCall.objects.filter(lead_id__in=clead_ids).count(),
            'activities': LeadActivity.objects.filter(lead_id__in=clead_ids).count(),
            'followups': {
                'pending': followup_qs.filter(status='pending').count(),
                'overdue': followup_qs.filter(
                    status='pending', scheduled_at__lt=timezone.now()
                ).count(),
                'completed': followup_qs.filter(status='completed').count(),
                'missed': followup_qs.filter(status='missed').count(),
            },
        },
    }


def leads_by_status_report(request):
    qs, _ = _scoped_leads(request)
    return _lead_dimension_breakdown(request, qs, 'status', 'status')


def leads_by_source_report(request):
    qs, _ = _scoped_leads(request)
    return _lead_dimension_breakdown(request, qs, 'source', 'source')


def leads_by_type_report(request):
    qs, _ = _scoped_leads(request)
    return _lead_dimension_breakdown(request, qs, 'lead_type', 'lead_type')


def leads_by_branch_report(request):
    qs, _ = _scoped_leads(request)
    return _lead_dimension_breakdown(request, qs, 'branch', 'branch')


def leads_by_campaign_report(request):
    qs, _ = _scoped_leads(request)
    return _lead_dimension_breakdown(request, qs, 'campaign', 'campaign')


def leads_by_counselor_report(request):
    qs, _ = _scoped_leads(request)
    return _lead_dimension_breakdown(request, qs, 'assigned_counselor', 'assigned_counselor')


def lead_trend_report(request):
    qs, _ = _scoped_leads(request)
    qs, start_date, end_date, error = _apply_lead_filters(request, qs)
    if error:
        return {'error': error}

    group_by = _query_param(request, 'group_by', 'month')
    if group_by not in ('day', 'week', 'month'):
        group_by = 'month'

    trunc = {'day': TruncDate, 'week': TruncWeek, 'month': TruncMonth}[group_by]

    rows = (
        qs.annotate(period=trunc('created_at'))
        .values('period')
        .annotate(
            leads=Count('id'),
            converted=Count('id', filter=Q(is_converted=True)),
        )
        .order_by('period')
    )

    lead_ids = qs.values_list('id', flat=True)
    payment_rows = (
        Payment.objects
        .filter(status='successful', enrollment__lead_id__in=lead_ids)
        .annotate(period=trunc('payment_date'))
        .values('period')
        .annotate(revenue=Coalesce(Sum('amount'), Decimal('0')))
        .order_by('period')
    )
    revenue_by_period = {r['period']: r['revenue'] for r in payment_rows}

    results = [
        {
            'period': r['period'].isoformat() if r['period'] else None,
            'leads': r['leads'],
            'converted': r['converted'],
            'revenue': str(revenue_by_period.get(r['period'], 0)),
        }
        for r in rows
    ]

    return {
        'group_by': group_by,
        'date_range': {
            'start_date': start_date.isoformat() if start_date else None,
            'end_date': end_date.isoformat() if end_date else None,
        },
        'results': results,
    }


def revenue_report(request):
    branch_id = _branch_scope(request)
    start_date, end_date, error = _resolve_date_range(request)
    if error:
        return {'error': error}

    payments = Payment.objects.filter(status='successful')
    if branch_id:
        payments = payments.filter(branch_id=branch_id)
    if start_date:
        payments = payments.filter(payment_date__date__gte=start_date)
    if end_date:
        payments = payments.filter(payment_date__date__lte=end_date)

    total_revenue = payments.aggregate(total=Coalesce(Sum('amount'), Decimal('0')))['total']

    by_method = list(
        payments.values('payment_method')
        .annotate(amount=Coalesce(Sum('amount'), Decimal('0')), count=Count('id'))
        .order_by('-amount')
    )

    by_branch = list(
        payments.values('branch__name')
        .annotate(amount=Coalesce(Sum('amount'), Decimal('0')), count=Count('id'))
        .order_by('-amount')
    )

    enrollments = Enrollment.objects.all()
    if branch_id:
        enrollments = enrollments.filter(branch_id=branch_id)
    if start_date:
        enrollments = enrollments.filter(enrollment_date__gte=start_date)
    if end_date:
        enrollments = enrollments.filter(enrollment_date__lte=end_date)
    enrollments = enrollments.exclude(
        status__in=['cancelled', 'dropped_out', 'transferred']
    ).annotate(
        paid=Coalesce(
            Sum('payments__amount', filter=Q(payments__status='successful')),
            Decimal('0'),
        ),
    ).prefetch_related('adjustments')

    # Same rule as Enrollment.remaining_amount. A credit (we owe them) is not
    # money to collect, so only positive balances count.
    outstanding = Decimal('0')
    for enrollment in enrollments:
        t = enrollment.adjustment_totals()
        owed = (
            enrollment.agreed_amount + t['extra_charge'] - t['discount']
            - t['write_off'] - enrollment.paid + t['refund']
        )
        outstanding += max(owed, Decimal('0'))

    return {
        'date_range': {
            'start_date': start_date.isoformat() if start_date else None,
            'end_date': end_date.isoformat() if end_date else None,
        },
        'summary': {
            'total_collected': str(total_revenue),
            'outstanding_to_collect': str(outstanding),
            'successful_payments': payments.count(),
        },
        'by_payment_method': by_method,
        'by_branch': by_branch,
    }


def enrollment_report(request):
    branch_id = _branch_scope(request)
    start_date, end_date, error = _resolve_date_range(request)
    if error:
        return {'error': error}

    enrollments = Enrollment.objects.all()
    if branch_id:
        enrollments = enrollments.filter(branch_id=branch_id)
    if start_date:
        enrollments = enrollments.filter(enrollment_date__gte=start_date)
    if end_date:
        enrollments = enrollments.filter(enrollment_date__lte=end_date)

    course_param = _query_param(request, 'course')
    status_param = _query_param(request, 'enrollment_status')
    if course_param:
        enrollments = enrollments.filter(course=course_param)
    if status_param:
        enrollments = enrollments.filter(status=status_param)

    by_course = list(
        enrollments.values('course__name')
        .annotate(
            count=Count('id'),
            agreed_total=Coalesce(Sum('agreed_amount'), Decimal('0')),
        )
        .order_by('-count')
    )

    by_status = list(
        enrollments.values('status')
        .annotate(count=Count('id'))
        .order_by('-count')
    )

    collected = (
        Payment.objects.filter(status='successful', enrollment__in=enrollments)
        .aggregate(total=Coalesce(Sum('amount'), Decimal('0')))['total']
    )

    totals = enrollments.aggregate(
        count=Count('id'),
        listed_total=Coalesce(Sum('listed_fee'), Decimal('0')),
        agreed_total=Coalesce(Sum('agreed_amount'), Decimal('0')),
    )

    return {
        'date_range': {
            'start_date': start_date.isoformat() if start_date else None,
            'end_date': end_date.isoformat() if end_date else None,
        },
        'summary': {
            'total_enrollments': totals['count'],
            'total_listed_fee': str(totals['listed_total']),
            'total_agreed_amount': str(totals['agreed_total']),
            'collected': str(collected),
        },
        'by_course': by_course,
        'by_status': by_status,
    }


def lead_conversion_report(request):
    qs, _ = _scoped_leads(request)
    qs, start_date, end_date, error = _apply_lead_filters(request, qs)
    if error:
        return {'error': error}

    lead_ids = qs.values_list('id', flat=True)
    total = qs.count()

    stages = {
        'total_leads': total,
        'contacted': LeadCall.objects.filter(lead_id__in=lead_ids).values('lead').distinct().count()
        if lead_ids else 0,
        'with_activity': LeadActivity.objects.filter(lead_id__in=lead_ids).values('lead').distinct().count()
        if lead_ids else 0,
        'interested': LeadCourseInterest.objects.filter(lead_id__in=lead_ids).values('lead').distinct().count()
        if lead_ids else 0,
        'enrolled': Enrollment.objects.filter(lead_id__in=lead_ids).count(),
        'converted': qs.filter(is_converted=True).count(),
        'lost': qs.filter(status__is_final=True).count(),
    }
    rates = {}
    for name, value in stages.items():
        if name == 'total_leads':
            continue
        rates[f'{name}_rate'] = round((value / total * 100), 1) if total else 0
    rates['conversion_rate'] = rates.get('converted_rate', 0)

    return {
        'date_range': {
            'start_date': start_date.isoformat() if start_date else None,
            'end_date': end_date.isoformat() if end_date else None,
        },
        'funnel': stages,
        'rates': rates,
    }


def counselor_performance_report(request):
    branch_id = _branch_scope(request)
    start_date, end_date, error = _resolve_date_range(request)
    if error:
        return {'error': error}

    counselors = User.objects.filter(assigned_leads__isnull=False).distinct()

    results = []
    for counselor in counselors:
        leads = Lead.objects.filter(assigned_counselor=counselor, is_deleted=False)
        if branch_id:
            leads = leads.filter(branch_id=branch_id)
        if start_date:
            leads = leads.filter(created_at__date__gte=start_date)
        if end_date:
            leads = leads.filter(created_at__date__lte=end_date)

        lead_ids = leads.values_list('id', flat=True)
        converted = leads.filter(is_converted=True).count()
        total = leads.count()

        collected = (
            Payment.objects.filter(
                status='successful', enrollment__lead__assigned_counselor=counselor
            )
        )
        if branch_id:
            collected = collected.filter(branch_id=branch_id)
        if start_date:
            collected = collected.filter(payment_date__date__gte=start_date)
        if end_date:
            collected = collected.filter(payment_date__date__lte=end_date)
        collected_total = collected.aggregate(total=Coalesce(Sum('amount'), Decimal('0')))['total']

        minutes_used = CallMinutesTransaction.objects.filter(user=counselor, type='deduction')
        if start_date:
            minutes_used = minutes_used.filter(created_at__date__gte=start_date)
        if end_date:
            minutes_used = minutes_used.filter(created_at__date__lte=end_date)
        seconds_used_total = -(
            minutes_used.aggregate(total=Coalesce(Sum('amount'), 0))['total'] or 0
        )

        results.append({
            'counselor': {
                'id': counselor.id,
                'name': counselor.name,
                'email': counselor.email,
            },
            'leads_assigned': total,
            'converted': converted,
            'conversion_rate': round((converted / total * 100), 1) if total else 0,
            'calls': LeadCall.objects.filter(lead_id__in=lead_ids).count() if lead_ids else 0,
            'activities': LeadActivity.objects.filter(lead_id__in=lead_ids).count() if lead_ids else 0,
            'followups_assigned': FollowUp.objects.filter(
                assigned_to=counselor, lead_id__in=lead_ids
            ).count() if lead_ids else 0,
            'followups_completed': FollowUp.objects.filter(
                assigned_to=counselor, lead_id__in=lead_ids, status='completed'
            ).count() if lead_ids else 0,
            'followups_pending': FollowUp.objects.filter(
                assigned_to=counselor, lead_id__in=lead_ids, status='pending'
            ).count() if lead_ids else 0,
            'enrollments': Enrollment.objects.filter(lead__assigned_counselor=counselor).count(),
            'revenue_collected': str(collected_total),
            'call_seconds_used': seconds_used_total,
            'seconds_remaining': counselor.call_seconds_balance,
            'call_minutes_used': round(seconds_used_total / 60, 1),
            'minutes_remaining': round(counselor.call_seconds_balance / 60, 1),
        })

    sort_key = _query_param(request, 'sort_by', 'leads_assigned')
    allowed = {
        'leads_assigned', 'converted', 'conversion_rate', 'calls',
        'activities', 'enrollments', 'revenue_collected',
        'call_minutes_used', 'minutes_remaining',
    }
    if sort_key not in allowed:
        sort_key = 'leads_assigned'
    results.sort(key=lambda r: r.get(sort_key), reverse=True)

    return {
        'date_range': {
            'start_date': start_date.isoformat() if start_date else None,
            'end_date': end_date.isoformat() if end_date else None,
        },
        'results': results,
    }

# ---------------------------------------------------------------- money owed

# How late a late part is, for the Who owes totals: (key, first day, last day).
LATE_BUCKETS = [
    ('days_1_30', 1, 30),
    ('days_31_60', 31, 60),
    ('days_61_90', 61, 90),
    ('over_90', 91, None),
]


def _owing_enrollments(request):
    """Confirmed, active and completed enrollments in the user's branch, ready for due_rows()."""
    branch_id = _branch_scope(request)
    qs = Enrollment.objects.filter(status__in=Enrollment.SEAT_STATUSES)
    if branch_id:
        qs = qs.filter(branch_id=branch_id)
    for name in ('course', 'batch'):
        value = _query_param(request, name)
        if value and str(value).isdigit():
            qs = qs.filter(**{f'{name}_id': int(value)})
    return qs.select_related('lead', 'student', 'course', 'batch', 'branch').annotate(
        paid_total=Coalesce(
            Sum('payments__amount', filter=Q(payments__status='successful')),
            Decimal('0'),
        ),
    ).prefetch_related(
        'adjustments',
        Prefetch('payment_plans', queryset=PaymentPlan.objects.prefetch_related('installments')),
    )


def _who(enrollment):
    student = enrollment.student
    return {
        'enrollment': enrollment.id,
        'lead': enrollment.lead_id,
        'name': student.full_name if student else enrollment.lead.full_name,
        'phone': (student.phone if student else None) or enrollment.lead.phone,
        'student_id': student.student_id if student else None,
        'course_name': enrollment.course.name,
        'batch_name': enrollment.batch.name if enrollment.batch else None,
        'branch_name': enrollment.branch.name if enrollment.branch else None,
    }


def who_owes_report(request):
    """Every student who owes money: how much, how much is late, and how late.

    Optional filters: course, batch (the money batch), late_only.
    """
    today = timezone.localdate()
    late_only = _to_bool(_query_param(request, 'late_only'))
    results = []
    totals = {'owed': Decimal('0'), 'late': Decimal('0')}
    buckets = {key: Decimal('0') for key, _, _ in LATE_BUCKETS}

    for enrollment in _owing_enrollments(request):
        rows = due_rows(enrollment, today=today, paid=enrollment.paid_total)
        open_rows = [r for r in rows if r['left'] > 0]
        late_rows = [r for r in open_rows if r['status'] == 'late']
        owed = sum((r['left'] for r in open_rows), Decimal('0'))
        if owed <= 0 or (late_only and not late_rows):
            continue
        late = sum((r['left'] for r in late_rows), Decimal('0'))
        upcoming = next((r for r in open_rows if r['status'] != 'late'), None)
        results.append({
            **_who(enrollment),
            'owed': owed,
            'late': late,
            'days_late': max((r['days_late'] for r in late_rows), default=0),
            'next_due_on': upcoming['due_on'] if upcoming else None,
            'next_due_amount': upcoming['left'] if upcoming else None,
        })
        totals['owed'] += owed
        totals['late'] += late
        for row in late_rows:
            for key, first, last in LATE_BUCKETS:
                if row['days_late'] >= first and (last is None or row['days_late'] <= last):
                    buckets[key] += row['left']

    results.sort(key=lambda r: (-r['days_late'], -r['owed'], r['name']))
    return {
        'as_of': today.isoformat(),
        'summary': {
            'students': len(results),
            'total_owed': str(totals['owed']),
            'total_late': str(totals['late']),
            'not_late': str(totals['owed'] - totals['late']),
            **{key: str(amount) for key, amount in buckets.items()},
        },
        'results': [
            {
                **r,
                'owed': str(r['owed']),
                'late': str(r['late']),
                'next_due_on': r['next_due_on'].isoformat() if r['next_due_on'] else None,
                'next_due_amount': (
                    str(r['next_due_amount']) if r['next_due_amount'] is not None else None
                ),
            }
            for r in results
        ],
    }


def _month_range(value, today):
    """First and last day of 'YYYY-MM', or of next month when value is empty."""
    if value:
        try:
            year, month = (int(part) for part in str(value).split('-'))
            first = date(year, month, 1)
        except (TypeError, ValueError):
            return None, None, 'Invalid month. Use YYYY-MM format.'
    else:
        first = date(today.year + today.month // 12, today.month % 12 + 1, 1)
    following = date(first.year + first.month // 12, first.month % 12 + 1, 1)
    return first, following - timedelta(days=1), None


def due_in_month_report(request):
    """Every unpaid part due in a month (next month by default), less what is paid on it.

    Optional filters: month (YYYY-MM), course, batch (the money batch).
    """
    today = timezone.localdate()
    first, last, error = _month_range(_query_param(request, 'month'), today)
    if error:
        return {'error': error}

    results = []
    for enrollment in _owing_enrollments(request):
        rows = due_rows(enrollment, today=today, paid=enrollment.paid_total)
        for row in rows:
            if row['left'] > 0 and row['due_on'] and first <= row['due_on'] <= last:
                results.append({
                    **_who(enrollment),
                    'kind': row['kind'],
                    'due_on': row['due_on'],
                    'amount': row['amount'],
                    'paid': row['paid'],
                    'left': row['left'],
                })

    results.sort(key=lambda r: (r['due_on'], r['name']))
    total = sum((r['left'] for r in results), Decimal('0'))
    return {
        'month': first.strftime('%Y-%m'),
        'start_date': first.isoformat(),
        'end_date': last.isoformat(),
        'summary': {
            'total_due': str(total),
            'parts': len(results),
            'students': len({r['enrollment'] for r in results}),
        },
        'results': [
            {
                **r,
                'due_on': r['due_on'].isoformat(),
                'amount': str(r['amount']),
                'paid': str(r['paid']),
                'left': str(r['left']),
            }
            for r in results
        ],
    }


# ---------------------------------------------------------------- batch profit


def _batch_row(batch, figures, closing):
    from apps.course.services import close_figures

    return {
        'batch': batch.pk,
        'coursebatch_id': batch.coursebatch_id,
        'name': batch.name,
        'course_name': batch.course.name,
        'status': batch.status,
        'status_display': batch.get_status_display(),
        'start_date': batch.start_date.isoformat() if batch.start_date else None,
        'end_date': batch.end_date.isoformat() if batch.end_date else None,
        **figures_json(figures),
        'closed_at': closing.created_at.isoformat() if closing else None,
        'at_close': figures_json(close_figures(closing)) if closing else None,
    }


def batch_profit_report(request):
    """Each batch's fees, collected, owed, costs and profit, newest batch first.

    Money counts on the batch the student first joined. A closed batch also
    has its figures at close. Optional filters: course, status.
    """
    branch_id = _branch_scope(request)
    batches = CourseBatch.objects.select_related('course')
    if branch_id:
        batches = batches.filter(branch_id=branch_id)
    course = _query_param(request, 'course')
    if course and str(course).isdigit():
        batches = batches.filter(course_id=int(course))
    status = _query_param(request, 'status')
    if status:
        if status not in CourseBatch.Status.values:
            return {'error': f'Unknown status: {status}.'}
        batches = batches.filter(status=status)
    batches = list(batches.order_by(F('start_date').desc(nulls_last=True), '-id'))

    figures = batch_figures([b.pk for b in batches])
    total = empty_figures()
    for f in figures.values():
        total['students'] += f['students']
        for name in MONEY_FIELDS:
            total[name] += f[name]
    closings = _last_closings(batches)
    return {
        'summary': {'batches': len(batches), **figures_json(total)},
        'results': [_batch_row(b, figures[b.pk], closings.get(b.pk)) for b in batches],
    }


def _last_closings(batches):
    """{batch id: its last close row}, for closed batches whose last row is a close."""
    from apps.course.models import BatchClosing

    closed = [b.pk for b in batches if b.status == CourseBatch.Status.CLOSED]
    last = {}
    for row in BatchClosing.objects.filter(course_batch_id__in=closed).order_by('created_at', 'id'):
        last[row.course_batch_id] = row
    return {pk: row for pk, row in last.items() if row.kind == BatchClosing.Kind.CLOSE}
