from django.core.management.base import BaseCommand
from django.db import transaction

from apps.lead.models import Enrollment, Lead
from apps.lead.services import student_for_lead
from apps.student.models import Student

# Enrollments that were already taken up before "Confirm enrollment" existed.
NOT_TAKEN_UP = (Enrollment.Status.PENDING, Enrollment.Status.CANCELLED)


class Command(BaseCommand):
    help = (
        'One-time fix for old data: give a student to every lead that has a '
        'confirmed, active or finished enrollment but no student yet, and link '
        'those enrollments. No invoices are made or emailed.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Show what would change without saving.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        leads = Lead._base_manager.filter(
            id__in=Enrollment.objects.exclude(status__in=NOT_TAKEN_UP).values('lead_id'),
        ).order_by('id')

        created = linked = 0
        for lead in leads:
            taken_up = lead.enrollments.exclude(status__in=NOT_TAKEN_UP).select_related('course')
            unlinked = taken_up.filter(student__isnull=True)
            has_student = Student.objects.filter(lead=lead).exists()
            if dry_run:
                created += not has_student
                linked += unlinked.count()
                continue
            with transaction.atomic():
                first = taken_up.order_by('enrollment_date', 'id').first()
                student, was_created = student_for_lead(lead, enrollment=first, user=None)
                created += was_created
                linked += unlinked.update(student=student)

        make, link = ('Would make', 'link') if dry_run else ('Made', 'linked')
        self.stdout.write(f'{make} {created} students and {link} {linked} enrollments.')
