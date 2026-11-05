import csv
from zoneinfo import ZoneInfo

from django.utils import timezone

from apps.callminutes.utils import format_duration


NEPAL_TZ = ZoneInfo('Asia/Kathmandu')
CALL_COLUMNS = [
    'Date & time (NPT)', 'Direction', 'Lead', 'Reference', 'Phone',
    'Caller', 'Logged by', 'Duration (MM:SS)', 'Seconds', 'Outcome',
    'Notes', 'Recording',
]


class CSVBuffer:
    def write(self, value):
        return value


def safe_cell(value):
    if value is None:
        return ''
    if isinstance(value, str) and (
        value.lstrip().startswith(('=', '+', '-', '@'))
        or value.startswith(('\t', '\r', '\n'))
    ):
        return "'" + value
    return value


def call_csv_rows(queryset):
    writer = csv.writer(CSVBuffer())
    yield '\ufeff'
    yield writer.writerow(CALL_COLUMNS)
    for call in queryset.iterator(chunk_size=2000):
        yield writer.writerow([safe_cell(value) for value in [
            timezone.localtime(call.call_timestamp, NEPAL_TZ).strftime('%Y-%m-%d %H:%M:%S'),
            call.get_direction_display(),
            call.lead.full_name,
            call.lead.reference_number,
            call.phone_number,
            call.caller,
            call.created_by.name if call.created_by else '',
            format_duration(call.duration_seconds),
            call.duration_seconds,
            call.outcome,
            call.notes,
            call.recording_url,
        ]])
