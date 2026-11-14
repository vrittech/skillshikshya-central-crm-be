"""
Helpers for the global call log (/api/calls/): Nepal-time date bounds, the
summary aggregation (US-1) and the CSV export rows (US-4).

Kept out of views.py so the viewset stays thin and the tricky parts
(time zones, CSV escaping) can be unit-tested on their own.
"""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework.exceptions import ValidationError

from apps.callminutes.utils import format_duration

# Business days are Nepal days, whatever settings.TIME_ZONE says (it is UTC).
NEPAL_TZ = ZoneInfo('Asia/Kathmandu')

# Never draw more than ~a year of empty day buckets for an open-ended range.
MAX_FILLED_DAYS = 366


# --------------------------------------------------------------- date bounds

def _parse_bound(value):
    """
    Parse a call_timestamp filter value.

    Returns ('date', date) for a bare YYYY-MM-DD, or ('datetime', aware dt)
    for a full ISO datetime. A datetime without an offset is read as Nepal
    time, because that is what a counselor in Nepal means.
    """
    raw = (value or '').strip()
    d = parse_date(raw) if len(raw) == 10 else None
    if d is not None:
        return 'date', d
    # An unencoded "+05:45" offset arrives as " 05:45" in a query string.
    if 'T' in raw and ' ' in raw:
        raw = raw.replace(' ', '+')
    try:
        dt = parse_datetime(raw)
    except ValueError:
        dt = None
    if dt is None:
        raise ValidationError({'call_timestamp': f'Invalid date or datetime: "{value}".'})
    if timezone.is_naive(dt):
        dt = dt.replace(tzinfo=NEPAL_TZ)
    return 'datetime', dt


def nepal_day_start(d):
    """00:00:00 Nepal time on day `d`, as an aware datetime."""
    return datetime.combine(d, time.min, tzinfo=NEPAL_TZ)


def lower_bound(value):
    """`call_timestamp__gte` value -> aware datetime (start of the NPT day for a bare date)."""
    kind, parsed = _parse_bound(value)
    return nepal_day_start(parsed) if kind == 'date' else parsed


def upper_bound_filter(value):
    """
    `call_timestamp__lte` value -> Q object.

    A bare date means "up to the end of that Nepal day", so it becomes
    `< 00:00 NPT of the next day`. That keeps calls at 23:59:59.5 too,
    which a `<= 23:59:59` bound would drop.
    """
    kind, parsed = _parse_bound(value)
    if kind == 'date':
        return Q(call_timestamp__lt=nepal_day_start(parsed + timedelta(days=1)))
    return Q(call_timestamp__lte=parsed)


def bound_to_nepal_date(value):
    """The Nepal calendar day a filter bound falls on, or None if absent/invalid."""
    if not value:
        return None
    try:
        kind, parsed = _parse_bound(value)
    except ValidationError:
        return None
    return parsed if kind == 'date' else parsed.astimezone(NEPAL_TZ).date()


# ------------------------------------------------------------------ summary

def _fill_days(counts_by_day, first, last):
    """Contiguous [{date, count}] from `first` to `last`, zero for empty days."""
    if first is None or last is None or first > last:
        return []
    if (last - first).days >= MAX_FILLED_DAYS:
        return [{'date': d.isoformat(), 'count': c} for d, c in sorted(counts_by_day.items())]
    out = []
    day = first
    while day <= last:
        out.append({'date': day.isoformat(), 'count': counts_by_day.get(day, 0)})
        day += timedelta(days=1)
    return out


def build_call_summary(queryset, date_from=None, date_to=None):
    """
    Totals, calls per Nepal day and the top 10 callers for an already
    filtered + branch-scoped queryset. Always exactly three queries.

    `date_from` / `date_to` are the NPT days of the active date filter; they
    only decide which empty days the chart shows, never which calls count.
    """
    qs = queryset.order_by()  # drop the default ordering; it breaks GROUP BY

    totals = qs.aggregate(
        total=Count('id'),
        inbound=Count('id', filter=Q(direction='inbound')),
        outbound=Count('id', filter=Q(direction='outbound')),
        connected=Count('id', filter=Q(duration_seconds__gt=0)),
        talk_time_seconds=Coalesce(Sum('duration_seconds'), 0),
    )
    total = totals['total']

    per_day_rows = (
        qs.annotate(day=TruncDate('call_timestamp', tzinfo=NEPAL_TZ))
        .values('day')
        .annotate(count=Count('id'))
        .order_by('day')
    )
    counts_by_day = {row['day']: row['count'] for row in per_day_rows}
    first = date_from or (min(counts_by_day) if counts_by_day else None)
    last = date_to or (max(counts_by_day) if counts_by_day else None)
    if date_to is None and date_from is not None:
        last = max([date_from, *counts_by_day.keys()])

    top_callers = [
        {
            'user_id': row['created_by'],
            'name': row['created_by__name'] or 'Unknown',
            'calls': row['calls'],
            'talk_time_seconds': row['talk_time_seconds'],
        }
        for row in (
            qs.values('created_by', 'created_by__name')
            .annotate(
                calls=Count('id'),
                talk_time_seconds=Coalesce(Sum('duration_seconds'), 0),
            )
            .order_by('-calls', '-talk_time_seconds', 'created_by__name')[:10]
        )
    ]

    return {
        'total_calls': total,
        'inbound_calls': totals['inbound'],
        'outbound_calls': totals['outbound'],
        'connected_calls': totals['connected'],
        'talk_time_seconds': totals['talk_time_seconds'],
        'avg_duration_seconds': round(totals['talk_time_seconds'] / total) if total else 0,
        'connect_rate': round(totals['connected'] * 100 / total, 1) if total else 0.0,
        'per_day': _fill_days(counts_by_day, first, last),
        'top_callers': top_callers,
    }


# ---------------------------------------------------------------- CSV export

CSV_HEADER = [
    'Date & time (NPT)', 'Direction', 'Lead', 'Reference', 'Phone', 'Caller',
    'Logged by', 'Duration (MM:SS)', 'Seconds', 'Outcome', 'Notes', 'Recording',
]

# A cell starting with one of these is run as a formula by Excel/Sheets
# (OWASP "CSV injection"). Tab and CR are included for the same reason.
_FORMULA_PREFIXES = ('=', '+', '-', '@', '\t', '\r')


def csv_safe(value):
    """Text for a CSV cell that a spreadsheet will never evaluate."""
    if value is None:
        return ''
    text = str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def call_to_csv_row(call):
    when = timezone.localtime(call.call_timestamp, NEPAL_TZ).strftime('%Y-%m-%d %H:%M:%S')
    lead = call.lead
    return [
        when,
        call.get_direction_display(),
        csv_safe(lead.full_name if lead else ''),
        csv_safe(lead.reference_number if lead else ''),
        csv_safe(call.phone_number),
        csv_safe(call.caller),
        csv_safe(call.created_by.name if call.created_by else ''),
        format_duration(call.duration_seconds),
        call.duration_seconds or 0,
        csv_safe(call.outcome),
        csv_safe(call.notes),
        csv_safe(call.recording_url),
    ]


def export_filename(today=None):
    today = today or timezone.localtime(timezone.now(), NEPAL_TZ).date()
    return f'calls-{today.isoformat()}.csv'

