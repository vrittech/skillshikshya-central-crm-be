from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Count, Q
from django.utils import timezone


class Student(models.Model):
    class Gender(models.TextChoices):
        MALE = 'male', 'Male'
        FEMALE = 'female', 'Female'
        OTHER = 'other', 'Other'

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        INACTIVE = 'inactive', 'Inactive'
        GRADUATED = 'graduated', 'Graduated'
        DROPPED = 'dropped', 'Dropped'
        TRANSFERRED = 'transferred', 'Transferred'

    student_id = models.CharField(max_length=20, unique=True, editable=False)
    full_name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20)
    secondary_phone = models.CharField(max_length=20, null=True, blank=True)
    email = models.EmailField(null=True, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=10, choices=Gender.choices, null=True, blank=True)
    address = models.TextField(null=True, blank=True)
    city = models.CharField(max_length=100, null=True, blank=True)
    state = models.CharField(max_length=100, null=True, blank=True)

    guardian_name = models.CharField(max_length=255, null=True, blank=True)
    guardian_phone = models.CharField(max_length=20, null=True, blank=True)
    guardian_relation = models.CharField(max_length=100, null=True, blank=True)

    emergency_contact_name = models.CharField(max_length=255, null=True, blank=True)
    emergency_contact_phone = models.CharField(max_length=20, null=True, blank=True)

    source = models.ForeignKey(
        'lead.LeadSource', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='students',
    )
    lead = models.ForeignKey(
        'lead.Lead', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='students',
    )
    branch = models.ForeignKey(
        'branch.Branch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='students',
    )
    assigned_counselor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='students_assigned',
    )
    enrolled_at = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    photo_url = models.URLField(max_length=500, null=True, blank=True)
    notes = models.TextField(null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='students_created',
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='students_updated',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'students'
        ordering = ['-created_at']
        permissions = [('move_branch', 'Can move student to another branch')]

    def __str__(self):
        return f'{self.student_id} - {self.full_name}'

    def save(self, *args, **kwargs):
        if not self.student_id:
            year = self.enrolled_at.year if self.enrolled_at else 2026
            prefix = f'STU-{year}'
            last = Student.objects.filter(student_id__startswith=prefix).order_by('student_id').last()
            seq = 1
            if last:
                seq = int(last.student_id.split('-')[-1]) + 1
            self.student_id = f'{prefix}-{seq:04d}'
        super().save(*args, **kwargs)

    def get_attendance_profile(self, course=None):
        from django.db.models import Count, Q

        filters = Q(student=self)
        if course:
            filters &= Q(course=course)

        total = Attendance.objects.filter(filters).count()
        present = Attendance.objects.filter(filters & Q(status='present')).count()
        absent = total - present
        pct = round((present / total * 100), 1) if total > 0 else 0

        present_dates = sorted(
            Attendance.objects.filter(filters & Q(status='present'))
            .values_list('date', flat=True)
        )

        current_streak = 0
        longest_streak = 0

        if present_dates:
            today = timezone.localdate()
            check = today
            while check in present_dates:
                current_streak += 1
                check -= timedelta(days=1)

            streak = 1
            for i in range(1, len(present_dates)):
                if (present_dates[i] - present_dates[i - 1]).days == 1:
                    streak += 1
                else:
                    longest_streak = max(longest_streak, streak)
                    streak = 1
            longest_streak = max(longest_streak, streak)

        monthly = (
            Attendance.objects.filter(filters)
            .values('date__year', 'date__month')
            .annotate(
                present_count=Count('id', filter=Q(status='present')),
                absent_count=Count('id', filter=Q(status='absent')),
            )
            .order_by('-date__year', '-date__month')
        )

        monthly_breakdown = [
            {
                'month': f'{m["date__year"]}-{str(m["date__month"]).zfill(2)}',
                'present': m['present_count'],
                'absent': m['absent_count'],
                'total': m['present_count'] + m['absent_count'],
                'percentage': round(
                    (m['present_count'] / (m['present_count'] + m['absent_count']) * 100), 1
                ) if (m['present_count'] + m['absent_count']) > 0 else 0,
            }
            for m in monthly
        ]

        return {
            'current_streak': current_streak,
            'longest_streak': longest_streak,
            'attendance_percentage': pct,
            'total_classes': total,
            'present_count': present,
            'absent_count': absent,
            'monthly_breakdown': monthly_breakdown,
        }


class StudentDocument(models.Model):
    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name='documents',
    )
    document_type = models.CharField(max_length=255)
    branch = models.ForeignKey(
        'branch.Branch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='student_documents',
    )
    file = models.URLField(max_length=500)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='student_docs_uploaded',
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'student_documents'
        ordering = ['-uploaded_at']

    def __str__(self):
        return f'{self.document_type} - {self.student.full_name}'


class Attendance(models.Model):
    class Status(models.TextChoices):
        PRESENT = 'present', 'Present'
        ABSENT = 'absent', 'Absent'

    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name='attendance_records',
    )
    course = models.ForeignKey(
        'course.Course', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='attendance_records',
    )
    date = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices)
    remarks = models.TextField(null=True, blank=True)
    marked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='attendance_marked',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'student_attendance'
        ordering = ['-date']
        unique_together = ['student', 'date', 'course']

    def __str__(self):
        return f'{self.student.full_name} - {self.date} ({self.get_status_display()})'

class BranchMove(models.Model):
    """One row each time a student moves to another branch. Rows are only added."""

    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name='branch_moves')
    from_branch = models.ForeignKey(
        'branch.Branch', on_delete=models.PROTECT, null=True, blank=True, related_name='+',
    )
    to_branch = models.ForeignKey('branch.Branch', on_delete=models.PROTECT, related_name='+')
    reason = models.TextField()
    moved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='student_branch_moves',
    )
    moved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'student_branch_moves'
        ordering = ['-moved_at', '-id']
        constraints = [
            models.CheckConstraint(check=~models.Q(reason=''), name='student_branch_move_reason_required'),
        ]

    def __str__(self):
        return f'{self.student_id}: {self.from_branch_id} -> {self.to_branch_id}'

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError('Branch moves cannot be changed.')
        self.reason = (self.reason or '').strip()
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Branch moves cannot be deleted.')
