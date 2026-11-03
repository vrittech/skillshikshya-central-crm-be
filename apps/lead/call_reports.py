import csv
from zoneinfo import ZoneInfo

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.callminutes.utils import format_duration

NPT = ZoneInfo('Asia/Kathmandu')
FORMULA_PREFIXES = ('=', '+', '-', '@', '\t', '\r')

CSV_HEADER = [
    'Date & time (NPT)', 'Direction', 'Lead', 'Reference', 'Phone', 'Caller',
    'Logged by', 'Duration (MM:SS)', 'Seconds', 'Outcome', 'Notes', 'Recording',
]


def build_summary(qs):
    """Totals, calls per day (Nepal time) and top 10 callers: 3 queries in total."""
    qs = qs.order_by()
    totals = qs.aggregate(
        total=Count('id'),
        inbound=Count('id', filter=Q(direction='inbound')),
        outbound=Count('id', filter=Q(direction='outbound')),
        talk=Sum('duration_seconds'),
        connected=Count('id', filter=Q(duration_seconds__gt=0)),
    )
    total = totals['total']
    talk = totals['talk'] or 0
    connected = totals['connected']

    per_day = (
        qs.annotate(day=TruncDate('call_timestamp', tzinfo=NPT))
        .values('day').annotate(count=Count('id')).order_by('day')
    )
    top = (
        qs.values('caller')
        .annotate(calls=Count('id'), talk_seconds=Sum('duration_seconds'))
        .order_by('-calls', 'caller')[:10]
    )
    return {
        'total_calls': total,
        'inbound': totals['inbound'],
        'outbound': totals['outbound'],
        'talk_seconds': talk,
        'average_seconds': round(talk / total) if total else 0,
        'connected_calls': connected,
        'connect_rate': round(connected * 100 / total, 1) if total else 0,
        'calls_per_day': [
            {'date': row['day'].isoformat(), 'count': row['count']} for row in per_day
        ],
        'top_callers': [
            {'caller': row['caller'], 'calls': row['calls'], 'talk_seconds': row['talk_seconds'] or 0}
            for row in top
        ],
    }


class _Echo:
    """File-like object that returns what is written, so rows can be streamed."""

    def write(self, value):
        return value


def _safe(value):
    """Stop spreadsheets from running a cell that starts with = + - @ as a formula."""
    if value is None:
        return ''
    text = str(value)
    return "'" + text if text.startswith(FORMULA_PREFIXES) else text


def csv_rows(qs):
    writer = csv.writer(_Echo())
    yield '\ufeff'  # BOM: makes Excel show Nepali (Devanagari) text correctly
    yield writer.writerow(CSV_HEADER)
    for c in qs.iterator(chunk_size=2000):
        when = timezone.localtime(c.call_timestamp, NPT).strftime('%Y-%m-%d %H:%M:%S')
        yield writer.writerow([_safe(v) for v in [
            when,
            c.direction,
            c.lead.full_name,
            c.lead.reference_number,
            c.phone_number,
            c.caller,
            c.created_by.name if c.created_by else '',
            format_duration(c.duration_seconds),
            c.duration_seconds,
            c.outcome,
            c.notes,
            c.recording_url,
        ]])


def export_filename():
    return f"calls-{timezone.localtime(timezone.now(), NPT).strftime('%Y-%m-%d')}.csv"
