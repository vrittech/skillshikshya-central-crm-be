from django.db import models
from django.conf import settings
from django.utils import timezone


def _generate_course_prefix(course_name):
    words = (course_name or '').upper().replace('/', ' ').split()
    prefix = ''.join(w[0] for w in words if w)[:3]
    return prefix or 'CRS'


class Course(models.Model):
    class CourseType(models.TextChoices):
        COURSE = 'course', 'Course'
        MASTER_CLASS = 'master_class', 'Master Class'
        JOB_READY_PROGRAM = 'job_ready_program', 'Job Ready Program'

    course_type = models.CharField(
        max_length=20, choices=CourseType.choices,
        null=True, blank=True, verbose_name='Course Type',
    )
    name = models.CharField(max_length=255)
    full_amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, default=None,
        verbose_name='Full Amount',
    )
    one_time_payment_discount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        verbose_name='One Time Payment Discount',
    )
    syllabus = models.URLField(max_length=500, null=True, blank=True)
    remarks = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    branch = models.ForeignKey(
        'branch.Branch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='courses',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='courses_created',
    )
    categories = models.ManyToManyField(
        'CourseCategory', related_name='courses', blank=True,
    )

    class Meta:
        db_table = 'courses'
        ordering = ['-created_at']

    def __str__(self):
        return self.name


class CourseCategory(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    branch = models.ForeignKey(
        'branch.Branch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='course_categories',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='course_categories_created',
    )

    class Meta:
        db_table = 'course_categories'
        ordering = ['name']

    def __str__(self):
        return self.name


class Batch(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    branch = models.ForeignKey(
        'branch.Branch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='batches',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='batches_created',
    )

    class Meta:
        db_table = 'batches'
        ordering = ['name']

    def __str__(self):
        return self.name


class CourseBatch(models.Model):
    class Mode(models.TextChoices):
        PHYSICAL = 'physical', 'Physical'
        ONLINE = 'online', 'Online'

    class Status(models.TextChoices):
        # Upcoming and running batches take students. A closed batch takes no
        # one new; its figures freeze at close (step 5).
        UPCOMING = 'upcoming', 'Upcoming'
        RUNNING = 'running', 'Running'
        CLOSED = 'closed', 'Closed'

    name = models.CharField(max_length=255)
    coursebatch_id = models.CharField(max_length=50, unique=True, editable=False)
    course = models.ForeignKey(
        Course, on_delete=models.CASCADE, related_name='course_batches',
    )
    batch = models.ForeignKey(
        Batch, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='course_batches',
    )
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    capacity = models.PositiveIntegerField(
        null=True, blank=True, help_text='Number of seats. Blank means no limit.',
    )
    mode = models.CharField(max_length=10, choices=Mode.choices, default=Mode.PHYSICAL)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.UPCOMING)
    trainer = models.CharField(max_length=255, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    branch = models.ForeignKey(
        'branch.Branch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='course_batches',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='course_batches_created',
    )

    class Meta:
        db_table = 'course_batches'
        ordering = ['-created_at']
        unique_together = ['course', 'batch', 'start_date']

    def save(self, *args, **kwargs):
        if not self.coursebatch_id:
            prefix = _generate_course_prefix(self.course.name if self.course else '')
            year = timezone.now().year
            last = CourseBatch.objects.filter(
                coursebatch_id__startswith=f'{prefix}-{year}-'
            ).order_by('coursebatch_id').last()
            if last:
                seq = int(last.coursebatch_id.rsplit('-', 1)[-1]) + 1
            else:
                seq = 1
            self.coursebatch_id = f'{prefix}-{year}-{seq:03d}'
        super().save(*args, **kwargs)

    def __str__(self):
        return self.coursebatch_id or self.name
