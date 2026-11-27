from django.db import migrations
from django.db.models import OuterRef, Subquery


def _fill(model, parent_model, parent_field):
    """Give rows without a branch the branch of the lead or enrollment they belong to."""
    parent_branch = parent_model.objects.filter(pk=OuterRef(parent_field)).values('branch')[:1]
    model.objects.filter(branch__isnull=True, **{f'{parent_field}__isnull': False}).update(
        branch=Subquery(parent_branch)
    )


def fill_branches(apps, schema_editor):
    Lead = apps.get_model('lead', 'Lead')
    Enrollment = apps.get_model('lead', 'Enrollment')
    Student = apps.get_model('student', 'Student')

    # Enrollments first: the money rows below take the enrollment's branch.
    _fill(Enrollment, Lead, 'lead')
    for name in ('Payment', 'Adjustment', 'PaymentPlan', 'TeamsLicence', 'ReminderLog'):
        _fill(apps.get_model('lead', name), Enrollment, 'enrollment')
    for name in ('LeadCourseInterest', 'LeadCall', 'LeadActivity', 'FollowUp', 'LeadDocument', 'VoiceNote'):
        _fill(apps.get_model('lead', name), Lead, 'lead')
    _fill(apps.get_model('student', 'StudentDocument'), Student, 'student')


class Migration(migrations.Migration):

    dependencies = [
        ('lead', '0035_batch_close_checklist'),
        ('student', '0004_backfill_lead_conversion'),
    ]

    operations = [
        migrations.RunPython(fill_branches, migrations.RunPython.noop),
    ]
