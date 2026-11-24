import re
from datetime import time

from django.db import migrations

# "6:00 AM – 8:00 AM", "6 am - 8:30 am", "5:30PM-7PM"
RANGE = re.compile(
    r'(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?\s*[–—-]\s*(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?',
    re.IGNORECASE,
)


def _time(hour, minute, half):
    hour, minute = int(hour), int(minute or 0)
    if not (1 <= hour <= 12 and 0 <= minute < 60):
        return None
    hour = hour % 12 + (12 if half.lower() == 'p' else 0)
    return time(hour, minute)


def fill_times(apps, schema_editor):
    """Old slots kept the time as text in description; read it into start and end."""
    Batch = apps.get_model('course', 'Batch')
    for slot in Batch.objects.filter(start_time__isnull=True, end_time__isnull=True):
        match = RANGE.search(slot.description or '')
        if not match:
            continue
        start = _time(*match.group(1, 2, 3))
        end = _time(*match.group(4, 5, 6))
        if start and end and end > start:
            slot.start_time, slot.end_time = start, end
            slot.save(update_fields=['start_time', 'end_time'])


class Migration(migrations.Migration):

    dependencies = [
        ('course', '0008_time_slot_times'),
    ]

    operations = [
        migrations.RunPython(fill_times, migrations.RunPython.noop),
    ]
