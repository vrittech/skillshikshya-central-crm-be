from datetime import datetime, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum, Count, Max
from django.db.models.fields.json import KeyTextTransform


class LearningMode(models.Model):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50, unique=True, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    branch = models.ForeignKey(
        'branch.Branch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='learning_modes',
    )

    class Meta:
        db_table = 'learning_modes'
        ordering = ['name']

    def __str__(self):
        return self.name


class Institution(models.Model):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50, unique=True, null=True, blank=True)
    address = models.TextField(null=True, blank=True)
    phone = models.CharField(max_length=20, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    branch = models.ForeignKey(
        'branch.Branch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='institutions',
    )

    class Meta:
        db_table = 'institutions'
        ordering = ['name']

    def __str__(self):
        return self.name


class Province(models.Model):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=20, unique=True, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    branch = models.ForeignKey(
        'branch.Branch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='provinces',
    )

    class Meta:
        db_table = 'provinces'
        ordering = ['name']

    def __str__(self):
        return self.name


class City(models.Model):
    name = models.CharField(max_length=255)
    province = models.ForeignKey(
        Province, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='cities',
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    branch = models.ForeignKey(
        'branch.Branch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='cities',
    )

    class Meta:
        db_table = 'cities'
        ordering = ['name']

    def __str__(self):
        return self.name


class LeadSource(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    is_protected = models.BooleanField(default=False)
    branch = models.ForeignKey(
        'branch.Branch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='lead_sources',
    )

    class Meta:
        db_table = 'lead_sources'
        ordering = ['name']

    def __str__(self):
        return self.name


class Campaign(models.Model):
    name = models.CharField(max_length=255)
    source = models.ForeignKey(
        LeadSource, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='campaigns',
    )
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    budget = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    branch = models.ForeignKey(
        'branch.Branch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='campaigns',
    )

    class Meta:
        db_table = 'campaigns'
        ordering = ['name']

    def __str__(self):
        return self.name


class LeadStatus(models.Model):
    name = models.CharField(max_length=255)
    color = models.CharField(max_length=7, default='#2563eb')
    sort_order = models.IntegerField(default=0)
    is_final = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    branch = models.ForeignKey(
        'branch.Branch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='lead_statuses',
    )

    class Meta:
        db_table = 'lead_statuses'
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.name


class LeadPriority(models.Model):
    name = models.CharField(max_length=255)
    color = models.CharField(max_length=7, default='#6b7280')
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    branch = models.ForeignKey(
        'branch.Branch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='lead_priorities',
    )

    class Meta:
        db_table = 'lead_priorities'
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.name


class LeadTag(models.Model):
    name = models.CharField(max_length=255)
    color = models.CharField(max_length=7, default='#2563eb')
    is_active = models.BooleanField(default=True)
    branch = models.ForeignKey(
        'branch.Branch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='lead_tags',
    )

    class Meta:
        db_table = 'lead_tags'
        ordering = ['name']

    def __str__(self):
        return self.name


class LeadType(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    branch = models.ForeignKey(
        'branch.Branch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='lead_types',
    )

    class Meta:
        db_table = 'lead_types'
        ordering = ['name']

    def __str__(self):
        return self.name


class Lead(models.Model):
    full_name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20, null=True, blank=True)
    secondary_phone = models.CharField(max_length=20, null=True, blank=True)
    email = models.EmailField(null=True, blank=True)
    address = models.TextField(null=True, blank=True)
    province = models.ForeignKey(
        Province, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='leads',
    )
    city = models.ForeignKey(
        City, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='leads',
    )
    landmark = models.CharField(max_length=255, null=True, blank=True)
    lead_type = models.ForeignKey(
        LeadType, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='leads',
    )
    reference_number = models.CharField(max_length=20, unique=True, editable=False)
    custom_data = models.JSONField(default=dict)
    source = models.ForeignKey(
        LeadSource, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='leads',
    )
    heard_from = models.CharField(max_length=500, null=True, blank=True)
    campaign = models.ForeignKey(
        Campaign, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='leads',
    )
    branch = models.ForeignKey(
        'branch.Branch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='leads',
    )
    priority = models.ForeignKey(
        LeadPriority, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='leads',
    )
    status = models.ForeignKey(
        LeadStatus, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='leads',
    )
    assigned_counselor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='assigned_leads',
    )
    lead_score = models.IntegerField(default=0)
    primary_course = models.ForeignKey(
        'course.Course', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='leads',
    )
    expected_revenue = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
    )
    next_followup_at = models.DateTimeField(null=True, blank=True)
    last_contact_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    tags = models.ManyToManyField(LeadTag, blank=True, related_name='leads')
    is_converted = models.BooleanField(default=False)
    is_archived = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='leads_deleted',
    )
    converted_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='leads_created',
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='leads_updated',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'leads'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['phone'], name='lead_phone_idx'),
            models.Index(fields=['email'], name='lead_email_idx'),
            models.Index(fields=['created_at'], name='lead_created_idx'),
            models.Index(
                KeyTextTransform('orbitchat_id', 'custom_data'),
                name='lead_orbitchat_id_idx',
            ),
        ]

    def __str__(self):
        return f'{self.reference_number} - {self.full_name} ({self.phone})'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._original_assigned_counselor_id = self.assigned_counselor_id

    def compute_lead_score(self):
        if not self.pk:
            return 0
        if self.is_converted:
            return 100
        if self.status and self.status.is_final:
            return 0
        score = 0
        if self.status:
            max_order = LeadStatus.objects.filter(is_final=False).aggregate(
                models.Max('sort_order')
            )['sort_order__max'] or 1
            score += min(35, int((self.status.sort_order / max_order) * 35))
        if self.priority:
            priority_map = {1: 25, 2: 17, 3: 9, 4: 3}
            score += priority_map.get(self.priority.sort_order, 0)
        if self.last_contact_at:
            days_since = (datetime.now(self.last_contact_at.tzinfo) - self.last_contact_at).days
            if days_since <= 3:
                score += 20
            elif days_since <= 7:
                score += 15
            elif days_since <= 14:
                score += 10
            elif days_since <= 30:
                score += 5
        call_count = self.calls.count()
        activity_count = self.activities.count()
        score += min(15, call_count * 3 + activity_count * 1)
        pending_future = self.followups.filter(
            status='pending', scheduled_at__gt=datetime.now(),
        ).exists()
        if pending_future:
            score += 5
        return max(0, min(100, round(score)))

    def save(self, *args, **kwargs):
        if not self.reference_number:
            year = datetime.now().year
            prefix = f'LD-{year}'
            last = Lead.objects.filter(reference_number__startswith=prefix).order_by('reference_number').last()
            seq = 1
            if last:
                seq = int(last.reference_number.split('-')[-1]) + 1
            self.reference_number = f'{prefix}-{seq:06d}'
        self.lead_score = self.compute_lead_score()
        super().save(*args, **kwargs)


class LeadCourseInterest(models.Model):
    class InterestLevel(models.TextChoices):
        HIGH = 'high', 'High'
        MEDIUM = 'medium', 'Medium'
        LOW = 'low', 'Low'

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='course_interests')
    course = models.ForeignKey(
        'course.Course', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='lead_interests',
    )
    course_name = models.CharField(max_length=255, null=True, blank=True)
    is_primary = models.BooleanField(default=False)
    interest_level = models.CharField(
        max_length=10, choices=InterestLevel.choices, default=InterestLevel.MEDIUM,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    branch = models.ForeignKey(
        'branch.Branch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='course_interests',
    )

    class Meta:
        db_table = 'lead_course_interests'
        ordering = ['-is_primary', '-created_at']

    def __str__(self):
        name = self.course.name if self.course else self.course_name
        return f'{self.lead.full_name} → {name or "?"}'


class Enrollment(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        CONFIRMED = 'confirmed', 'Confirmed'
        ACTIVE = 'active', 'Active'
        COMPLETED = 'completed', 'Completed'
        CANCELLED = 'cancelled', 'Cancelled'
        DROPPED_OUT = 'dropped_out', 'Dropped Out'
        TRANSFERRED = 'transferred', 'Transferred'

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='enrollments')
    student = models.ForeignKey(
        'student.Student', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='enrollments',
    )
    course = models.ForeignKey('course.Course', on_delete=models.CASCADE, related_name='enrollments')
    batch = models.ForeignKey(
        'course.CourseBatch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='enrollments',
    )
    enrollment_date = models.DateField()
    status = models.CharField(
        max_length=15, choices=Status.choices, default=Status.PENDING,
    )
    listed_fee = models.DecimalField(max_digits=12, decimal_places=2)
    standard_discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    additional_discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    agreed_amount = models.DecimalField(max_digits=12, decimal_places=2)
    remarks = models.TextField(null=True, blank=True)
    branch = models.ForeignKey(
        'branch.Branch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='enrollments',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='enrollments_created',
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='enrollments_updated',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'enrollments'
        ordering = ['-enrollment_date']

    def __str__(self):
        return f'{self.lead.reference_number} → {self.course.name} ({self.status})'

    @property
    def total_paid(self):
        result = self.payments.filter(status='successful').aggregate(Sum('amount'))
        return result['amount__sum'] or 0

    @property
    def remaining_amount(self):
        return self.agreed_amount - self.total_paid

    @property
    def is_full_paid(self):
        return self.remaining_amount <= 0


class Payment(models.Model):
    class Method(models.TextChoices):
        CASH = 'cash', 'Cash'
        ESEWA = 'esewa', 'eSewa'
        KHALTI = 'khalti', 'Khalti'
        BANK_TRANSFER = 'bank_transfer', 'Bank Transfer'
        CARD = 'card', 'Card'
        CONNECT_IPS = 'connectips', 'ConnectIPS'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        SUCCESSFUL = 'successful', 'Successful'
        FAILED = 'failed', 'Failed'
        REFUNDED = 'refunded', 'Refunded'
        VOIDED = 'voided', 'Voided'

    enrollment = models.ForeignKey(
        Enrollment, on_delete=models.CASCADE, related_name='payments',
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_date = models.DateTimeField()
    payment_method = models.CharField(
        max_length=20, choices=Method.choices, default=Method.CASH,
    )
    transaction_reference = models.CharField(max_length=255, null=True, blank=True)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING,
    )
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='payments_received',
    )
    notes = models.TextField(null=True, blank=True)
    branch = models.ForeignKey(
        'branch.Branch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='payments',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'payments'
        ordering = ['-payment_date']

    def __str__(self):
        return f'{self.amount} ({self.get_status_display()}) — {self.enrollment}'


class EnrollmentHistory(models.Model):
    """Reason trail: one row per major change to an enrollment.

    Rows are only ever added. Editing or deleting one raises ValidationError.
    """

    class Action(models.TextChoices):
        CANCEL = 'cancel', 'Cancel'
        REINSTATE = 'reinstate', 'Reinstate'
        SWITCH_BATCH = 'switch_batch', 'Switch batch'
        PAUSE = 'pause', 'Pause'
        RESUME = 'resume', 'Resume'
        CHANGE_FEE = 'change_fee', 'Change fee'
        EXTRA_CHARGE = 'extra_charge', 'Extra charge'
        DISCOUNT = 'discount', 'Discount'
        REFUND = 'refund', 'Refund'
        WRITE_OFF = 'write_off', 'Write-off'
        VOID_PAYMENT = 'void_payment', 'Void payment'
        CANCEL_INVOICE = 'cancel_invoice', 'Cancel invoice'
        CHANGE_DUE_DATES = 'change_due_dates', 'Change due dates'

    enrollment = models.ForeignKey(
        Enrollment, on_delete=models.PROTECT, related_name='history',
    )
    action = models.CharField(max_length=20, choices=Action.choices)
    reason = models.TextField()
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='enrollment_changes',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'enrollment_history'
        ordering = ['-created_at']
        constraints = [
            models.CheckConstraint(
                check=~models.Q(reason=''),
                name='enrollment_history_reason_required',
            ),
        ]

    def __str__(self):
        return f'{self.get_action_display()} on enrollment {self.enrollment_id}'

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError('History rows cannot be changed.')
        self.reason = (self.reason or '').strip()
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('History rows cannot be deleted.')

    @classmethod
    def record(cls, *, enrollment, action, reason, performed_by, before=None, after=None):
        return cls.objects.create(
            enrollment=enrollment,
            action=action,
            reason=reason,
            performed_by=performed_by,
            before=before or {},
            after=after or {},
        )


class LeadCall(models.Model):
    class Direction(models.TextChoices):
        INBOUND = 'inbound', 'Inbound'
        OUTBOUND = 'outbound', 'Outbound'

    lead = models.ForeignKey(
        Lead, on_delete=models.CASCADE, related_name='calls',
    )
    call_timestamp = models.DateTimeField()
    direction = models.CharField(
        max_length=10, choices=Direction.choices, default=Direction.OUTBOUND,
    )
    caller = models.CharField(max_length=255)
    phone_number = models.CharField(max_length=20)
    duration_seconds = models.IntegerField(default=0)
    outcome = models.CharField(max_length=255, null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    branch = models.ForeignKey(
        'branch.Branch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='lead_calls',
    )
    recording_url = models.URLField(max_length=500, null=True, blank=True)
    next_followup_at = models.DateTimeField(null=True, blank=True)
    next_action = models.TextField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='lead_calls_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'lead_calls'
        ordering = ['-call_timestamp']

    def __str__(self):
        return f'{self.get_direction_display()} - {self.caller} ({self.call_timestamp})'


class LeadActivity(models.Model):
    class ActivityType(models.TextChoices):
        CALL = 'call', 'Call'
        WHATSAPP = 'whatsapp', 'WhatsApp'
        NOTE = 'note', 'Note'
        MEETING = 'meeting', 'Meeting'
        STATUS_CHANGE = 'status_change', 'Status Change'
        ASSIGNMENT_CHANGE = 'assignment_change', 'Assignment Change'
        DEMO_CLASS = 'demo_class', 'Demo Class'
        PAYMENT = 'payment', 'Payment'
        ENROLLMENT = 'enrollment', 'Enrollment'

    lead = models.ForeignKey(
        Lead, on_delete=models.CASCADE, related_name='activities',
    )
    activity_type = models.CharField(
        max_length=20, choices=ActivityType.choices,
    )
    title = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    branch = models.ForeignKey(
        'branch.Branch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='lead_activities',
    )
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='activities_performed',
    )
    activity_at = models.DateTimeField()
    metadata = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'lead_activities'
        ordering = ['-activity_at']

    def __str__(self):
        return f'{self.get_activity_type_display()} - {self.title}'


class FollowUp(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        COMPLETED = 'completed', 'Completed'
        MISSED = 'missed', 'Missed'
        CANCELLED = 'cancelled', 'Cancelled'

    lead = models.ForeignKey(
        Lead, on_delete=models.CASCADE, related_name='followups',
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='followups_assigned',
    )
    scheduled_at = models.DateTimeField()
    purpose = models.TextField(null=True, blank=True)
    reminder_at = models.DateTimeField(null=True, blank=True)
    reminder_sent = models.BooleanField(default=False)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING,
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    branch = models.ForeignKey(
        'branch.Branch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='followups',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='followups_created',
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='followups_updated',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'lead_followups'
        ordering = ['-scheduled_at']

    def __str__(self):
        return f'FollowUp for {self.lead.full_name} at {self.scheduled_at}'


class LeadDocument(models.Model):
    lead = models.ForeignKey(
        Lead, on_delete=models.CASCADE, related_name='documents',
    )
    document_type = models.CharField(max_length=255)
    branch = models.ForeignKey(
        'branch.Branch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='lead_documents',
    )
    file = models.URLField(max_length=500)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='documents_uploaded',
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'lead_documents'
        ordering = ['-uploaded_at']

    def __str__(self):
        return f'{self.document_type} - {self.lead.full_name}'


class Notification(models.Model):
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='notifications',
    )
    title = models.CharField(max_length=255)
    message = models.TextField()
    link = models.CharField(max_length=500, null=True, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'notifications'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.title} → {self.recipient.email}'
