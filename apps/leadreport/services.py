from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce, TruncDate, TruncMonth, TruncWeek
from django.utils import timezone

from apps.lead.models import (
    Enrollment, FollowUp, Lead, LeadActivity, LeadCall,
    LeadCourseInterest, Payment,
)
from apps.callminutes.models import CallMinutesTransaction
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
            'revenue_collected': format(revenue_collected, '.2f'),
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
    )

    outstanding = 0
    for enrollment in enrollments:
        outstanding += (enrollment.agreed_amount - enrollment.paid)

    return {
        'date_range': {
            'start_date': start_date.isoformat() if start_date else None,
            'end_date': end_date.isoformat() if end_date else None,
        },
        'summary': {
            'total_collected': format(total_revenue, '.2f'),
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
            'collected': format(collected, '.2f'),
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