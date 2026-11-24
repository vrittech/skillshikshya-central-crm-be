# Fills the new enquiry fields on old course interests.

from django.db import migrations


SEAT_STATUSES = ('confirmed', 'active', 'completed')


def fill_enquiries(apps, schema_editor):
    """Old course interests get the lead's source and counselor. A lead with a
    confirmed enrollment has a won enquiry for that course (one is made if missing)."""
    Interest = apps.get_model('lead', 'LeadCourseInterest')
    Change = apps.get_model('lead', 'EnquiryStageChange')
    Enrollment = apps.get_model('lead', 'Enrollment')

    for interest in Interest.objects.select_related('lead').iterator():
        lead = interest.lead
        interest.source_id = interest.source_id or lead.source_id
        interest.counselor_id = interest.counselor_id or lead.assigned_counselor_id
        interest.branch_id = interest.branch_id or lead.branch_id
        interest.created_by_id = interest.created_by_id or lead.created_by_id
        interest.stage_changed_at = interest.created_at
        interest.save(update_fields=[
            'source', 'counselor', 'branch', 'created_by', 'stage_changed_at',
        ])

    enrollments = Enrollment.objects.filter(status__in=SEAT_STATUSES).select_related('lead')
    for enrollment in enrollments.order_by('created_at'):
        lead = enrollment.lead
        interest = (
            Interest.objects.filter(lead=lead, course_id=enrollment.course_id, enrollment__isnull=True)
            .exclude(stage='won').order_by('-is_primary', '-created_at').first()
        )
        if interest is None:
            interest = Interest.objects.create(
                lead=lead,
                course_id=enrollment.course_id,
                is_primary=not Interest.objects.filter(lead=lead).exists(),
                source_id=lead.source_id,
                counselor_id=lead.assigned_counselor_id,
                branch_id=enrollment.branch_id or lead.branch_id,
                created_by_id=lead.created_by_id,
            )
        from_stage = interest.stage
        interest.stage = 'won'
        interest.enrollment = enrollment
        interest.stage_changed_at = enrollment.updated_at
        interest.save(update_fields=['stage', 'enrollment', 'stage_changed_at'])
        Change.objects.create(
            enquiry=interest, from_stage=from_stage, to_stage='won',
            note='Enrollment confirmed before enquiries had stages.',
        )



class Migration(migrations.Migration):

    dependencies = [
        ('lead', '0026_enquiry_stages'),
    ]

    operations = [
        migrations.RunPython(fill_enquiries, migrations.RunPython.noop),
    ]
