"""Fix the next follow-up and last contacted dates saved before follow-ups kept them current.

Next follow-up = the earliest pending follow-up (empty when none). Last
contacted moves up to the latest completed follow-up; it never moves back.
"""
from django.db import migrations
from django.db.models import Max, Min, Q


def sync(apps, schema_editor):
    Lead = apps.get_model('lead', 'Lead')
    FollowUp = apps.get_model('lead', 'FollowUp')
    rows = FollowUp.objects.values('lead_id').annotate(
        next=Min('scheduled_at', filter=Q(status='pending')),
        done=Max('completed_at', filter=Q(status='completed')),
    ).order_by()
    for row in rows.iterator():
        Lead.objects.filter(pk=row['lead_id']).update(next_followup_at=row['next'])
        if row['done']:
            Lead.objects.filter(pk=row['lead_id']).filter(
                Q(last_contact_at__isnull=True) | Q(last_contact_at__lt=row['done']),
            ).update(last_contact_at=row['done'])


class Migration(migrations.Migration):

    dependencies = [
        ('lead', '0030_lead_changes'),
    ]

    operations = [
        migrations.RunPython(sync, migrations.RunPython.noop),
    ]
