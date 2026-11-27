from datetime import datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import models
from django.db.models import Sum, Count, Max
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Lower
from django.utils import timezone


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
        self._original_status_id = self.status_id
        # Who is saving; set by the view or the admin. Empty = a job or the shell.
        self._changed_by = None

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
            status='pending', scheduled_at__gt=timezone.now(),
        ).exists()
        if pending_future:
            score += 5
        return max(0, min(100, round(score)))

    def save(self, *args, **kwargs):
        if not self.reference_number:
            year = timezone.localdate().year
            prefix = f'LD-{year}'
            last = Lead.objects.filter(reference_number__startswith=prefix).order_by('reference_number').last()
            seq = 1
            if last:
                seq = int(last.reference_number.split('-')[-1]) + 1
            self.reference_number = f'{prefix}-{seq:06d}'
        self.lead_score = self.compute_lead_score()
        adding = self._state.adding
        super().save(*args, **kwargs)
        if not adding:
            self._record_changes(kwargs.get('update_fields'))
        self._original_status_id = self.status_id
        self._original_assigned_counselor_id = self.assigned_counselor_id

    def sync_followup_dates(self):
        """Keep the next follow-up and last contacted dates in step with the follow-ups.

        The next follow-up is the earliest pending one (empty when none is
        pending). A completed follow-up counts as contact, so last contacted
        moves up to it; it never moves back.
        """
        dates = self.followups.aggregate(
            next=models.Min('scheduled_at', filter=models.Q(status=FollowUp.Status.PENDING)),
            done=Max('completed_at', filter=models.Q(status=FollowUp.Status.COMPLETED)),
        )
        self.next_followup_at = dates['next']
        if dates['done'] and (self.last_contact_at is None or dates['done'] > self.last_contact_at):
            self.last_contact_at = dates['done']
        self.save(update_fields=['next_followup_at', 'last_contact_at', 'lead_score'])

    def refresh_from_db(self, *args, **kwargs):
        super().refresh_from_db(*args, **kwargs)
        if kwargs.get('fields') is None and not args[1:]:
            self._original_status_id = self.status_id
            self._original_assigned_counselor_id = self.assigned_counselor_id

    def _record_changes(self, update_fields):
        """A LeadChange row for a new status or counselor (see LeadChange)."""
        saved = None if update_fields is None else set(update_fields)
        tracked = [
            (LeadChange.Field.STATUS, 'status', self._original_status_id, self.status_id, LeadStatus),
            (
                LeadChange.Field.COUNSELOR, 'assigned_counselor',
                self._original_assigned_counselor_id, self.assigned_counselor_id,
                type(self)._meta.get_field('assigned_counselor').related_model,
            ),
        ]
        for field, name, old_id, new_id, model in tracked:
            if old_id == new_id or (saved is not None and not saved & {name, f'{name}_id'}):
                continue
            names = dict(model.objects.filter(pk__in=[old_id, new_id]).values_list('pk', 'name'))
            LeadChange.objects.create(
                lead=self, field=field,
                from_value=names.get(old_id, ''), to_value=names.get(new_id, ''),
                changed_by=self._changed_by,
            )


class LeadChange(models.Model):
    """One row each time a lead's status or counselor changes. Rows are only ever added.

    Lead.save() writes them, so every way of editing a lead is covered. The
    names are kept as they were then, so a renamed status or user doesn't
    rewrite the past.
    """

    class Field(models.TextChoices):
        STATUS = 'status', 'Status'
        COUNSELOR = 'counselor', 'Counselor'

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='changes')
    field = models.CharField(max_length=10, choices=Field.choices)
    from_value = models.CharField(max_length=255, blank=True, default='')
    to_value = models.CharField(max_length=255, blank=True, default='')
    # Empty when a job or the shell made the change.
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        null=True, blank=True, related_name='lead_changes',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'lead_changes'
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f'{self.get_field_display()}: {self.from_value} → {self.to_value} on lead {self.lead_id}'

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError('Lead changes cannot be edited.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Lead changes cannot be deleted.')


class LeadCourseInterest(models.Model):
    """An enquiry: one person asking about one course.

    Each enquiry has its own stage, counselor and source. The stage changes
    only through apps.lead.enquiries, which saves an EnquiryStageChange row
    each time. Won is set when an enrollment for the course is confirmed.
    """

    class InterestLevel(models.TextChoices):
        HIGH = 'high', 'High'
        MEDIUM = 'medium', 'Medium'
        LOW = 'low', 'Low'

    class Stage(models.TextChoices):
        NEW = 'new', 'New'
        CONTACTED = 'contacted', 'Contacted'
        INTERESTED = 'interested', 'Interested'
        WON = 'won', 'Won'
        LOST = 'lost', 'Lost'

    class LostReason(models.TextChoices):
        TOO_EXPENSIVE = 'too_expensive', 'Too expensive'
        OTHER_INSTITUTE = 'other_institute', 'Chose another institute'
        TIMING = 'timing', "Timing doesn't suit"
        NOT_REACHABLE = 'not_reachable', "Can't reach"
        NOT_INTERESTED = 'not_interested', 'Not interested'
        OTHER = 'other', 'Other'

    OPEN_STAGES = (Stage.NEW, Stage.CONTACTED, Stage.INTERESTED)
    # The order an enquiry goes through. Lost can happen at any open stage.
    FUNNEL = (Stage.NEW, Stage.CONTACTED, Stage.INTERESTED, Stage.WON)

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='course_interests')
    course = models.ForeignKey(
        'course.Course', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='lead_interests',
    )
    course_name = models.CharField(max_length=255, null=True, blank=True)
    # What the person asked about, and the counselor's notes on it. The notes
    # can also be a recording: a VoiceNote on this enquiry.
    about = models.CharField(max_length=255, blank=True, default='')
    description = models.TextField(blank=True, default='')
    is_primary = models.BooleanField(default=False)
    interest_level = models.CharField(
        max_length=10, choices=InterestLevel.choices, default=InterestLevel.MEDIUM,
    )
    stage = models.CharField(
        max_length=12, choices=Stage.choices, default=Stage.NEW, db_index=True,
    )
    stage_changed_at = models.DateTimeField(default=timezone.now)
    counselor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='enquiries',
    )
    source = models.ForeignKey(
        LeadSource, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='enquiries',
    )
    lost_reason = models.CharField(
        max_length=20, choices=LostReason.choices, null=True, blank=True,
    )
    lost_note = models.TextField(blank=True, default='')
    # The open stage the enquiry was in when it was lost: where it died.
    lost_at_stage = models.CharField(
        max_length=12, choices=Stage.choices, null=True, blank=True,
    )
    # The confirmed enrollment that won it.
    enrollment = models.ForeignKey(
        'Enrollment', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='enquiries',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='enquiries_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    branch = models.ForeignKey(
        'branch.Branch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='course_interests',
    )

    class Meta:
        db_table = 'lead_course_interests'
        ordering = ['-is_primary', '-created_at']
        constraints = [
            models.CheckConstraint(
                check=~models.Q(stage='lost') | models.Q(
                    lost_reason__isnull=False, lost_at_stage__isnull=False,
                ),
                name='lost_enquiry_has_reason_and_stage',
            ),
        ]

    @property
    def label(self):
        """The course name, or what the enquiry is about when it has no course."""
        if self.course:
            return self.course.name
        return self.course_name or self.about or ''

    def __str__(self):
        return f'{self.lead.full_name} → {self.label or "?"}'


class EnquiryStageChange(models.Model):
    """One row each time an enquiry's stage changes. Rows are only ever added."""

    enquiry = models.ForeignKey(
        LeadCourseInterest, on_delete=models.CASCADE, related_name='stage_changes',
    )
    from_stage = models.CharField(max_length=12, choices=LeadCourseInterest.Stage.choices)
    to_stage = models.CharField(max_length=12, choices=LeadCourseInterest.Stage.choices)
    note = models.TextField(blank=True, default='')
    # Empty only when the website form or a job made the change.
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        null=True, blank=True, related_name='enquiry_stage_changes',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'enquiry_stage_changes'
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f'{self.from_stage} → {self.to_stage} on enquiry {self.enquiry_id}'

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError('Stage changes cannot be edited.')
        self.note = (self.note or '').strip()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Stage changes cannot be deleted.')


class Enrollment(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        CONFIRMED = 'confirmed', 'Confirmed'
        ACTIVE = 'active', 'Active'
        COMPLETED = 'completed', 'Completed'
        CANCELLED = 'cancelled', 'Cancelled'
        DROPPED_OUT = 'dropped_out', 'Dropped Out'
        TRANSFERRED = 'transferred', 'Transferred'

    # Enrollments in these statuses take a seat in their current batch.
    SEAT_STATUSES = (Status.CONFIRMED, Status.ACTIVE, Status.COMPLETED)

    # PROTECT: a lead, course or batch with enrollments cannot be deleted, so
    # money records are never lost through a cascade.
    lead = models.ForeignKey(Lead, on_delete=models.PROTECT, related_name='enrollments')
    student = models.ForeignKey(
        'student.Student', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='enrollments',
    )
    course = models.ForeignKey('course.Course', on_delete=models.PROTECT, related_name='enrollments')
    # The money batch: the batch the student first joined. Fees and revenue
    # count here, and it does not change when the student moves.
    batch = models.ForeignKey(
        'course.CourseBatch', on_delete=models.PROTECT,
        null=True, blank=True, related_name='enrollments',
    )
    # Where the student sits now. Empty when not in a batch (paused, left or
    # not placed yet). Only the seat actions change it, each adding a
    # Placement row.
    current_batch = models.ForeignKey(
        'course.CourseBatch', on_delete=models.PROTECT,
        null=True, blank=True, related_name='seated_enrollments',
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

    def adjustment_totals(self):
        """Sum of each money-entry kind. Uses the prefetch cache when the view loaded it."""
        totals = {kind: Decimal('0') for kind in Adjustment.Kind.values}
        for adjustment in self.adjustments.all():
            totals[adjustment.kind] += adjustment.amount
        return totals

    @property
    def extra_charges_total(self):
        return self.adjustment_totals()[Adjustment.Kind.EXTRA_CHARGE]

    @property
    def discounts_total(self):
        return self.adjustment_totals()[Adjustment.Kind.DISCOUNT]

    @property
    def write_offs_total(self):
        return self.adjustment_totals()[Adjustment.Kind.WRITE_OFF]

    @property
    def refunds_total(self):
        return self.adjustment_totals()[Adjustment.Kind.REFUND]

    @property
    def remaining_amount(self):
        """What the student owes now. Below 0 means we owe them (credit).

        agreed + extra charges - discounts - write-offs - paid + refunds
        """
        t = self.adjustment_totals()
        return (
            self.agreed_amount
            + t[Adjustment.Kind.EXTRA_CHARGE]
            - t[Adjustment.Kind.DISCOUNT]
            - t[Adjustment.Kind.WRITE_OFF]
            - self.total_paid
            + t[Adjustment.Kind.REFUND]
        )

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
        Enrollment, on_delete=models.PROTECT, related_name='payments',
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
    # Set only by services.void_payment(); a voided payment never counts as paid.
    void_reason = models.TextField(null=True, blank=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        null=True, blank=True, related_name='payments_voided',
    )
    voided_at = models.DateTimeField(null=True, blank=True)
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


class Adjustment(models.Model):
    """A money entry on an enrollment besides payments. Rows are only ever added.

    Changing the price, giving money back or forgiving debt each adds a row with
    a reason, instead of editing the agreed amount. See Enrollment.remaining_amount.
    """

    class Kind(models.TextChoices):
        EXTRA_CHARGE = 'extra_charge', 'Extra charge'
        DISCOUNT = 'discount', 'Discount'
        WRITE_OFF = 'write_off', 'Write-off'
        REFUND = 'refund', 'Refund'

    enrollment = models.ForeignKey(
        Enrollment, on_delete=models.PROTECT, related_name='adjustments',
    )
    kind = models.CharField(max_length=15, choices=Kind.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.TextField()
    branch = models.ForeignKey(
        'branch.Branch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='adjustments',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='adjustments_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'enrollment_adjustments'
        verbose_name = 'money entry'
        verbose_name_plural = 'money entries'
        ordering = ['-created_at']
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount__gt=0), name='adjustment_amount_positive',
            ),
            models.CheckConstraint(
                check=~models.Q(reason=''), name='adjustment_reason_required',
            ),
        ]

    def __str__(self):
        return f'{self.get_kind_display()} {self.amount} on enrollment {self.enrollment_id}'

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError('Money entries cannot be changed.')
        self.reason = (self.reason or '').strip()
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Money entries cannot be deleted.')


class EnrollmentHistory(models.Model):
    """Reason trail: one row per major change to an enrollment.

    Rows are only ever added. Editing or deleting one raises ValidationError.
    """

    class Action(models.TextChoices):
        CONFIRM = 'confirm', 'Confirm'
        JOIN_BATCH = 'join_batch', 'Join batch'
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
        GIVE_LICENCE = 'give_licence', 'Teams licence given'
        FREE_LICENCE = 'free_licence', 'Teams licence freed'
        PAUSE_LICENCE = 'pause_licence', 'Teams licence paused'
        RESUME_LICENCE = 'resume_licence', 'Teams licence back in use'
        COMPLETE = 'complete', 'Completed'
        UNDO_COMPLETE = 'undo_complete', 'Completion undone'
        CHANGE_STATUS = 'change_status', 'Status changed'
        MOVE_BRANCH = 'move_branch', 'Moved branch'

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
        verbose_name_plural = 'enrollment history'
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


class Placement(models.Model):
    """Seat history: one row each time an enrollment's seat changes.

    The newest row says where the student sits now, which is also kept on
    Enrollment.current_batch. A row with no batch means they sit nowhere:
    paused or left. Rows are only ever added. Editing or deleting one raises
    ValidationError.
    """

    class Kind(models.TextChoices):
        JOIN = 'join', 'Joined'
        MOVE = 'move', 'Moved'
        PAUSE = 'pause', 'Paused'
        RESUME = 'resume', 'Resumed'
        LEAVE = 'leave', 'Left'

    TAKES_SEAT = (Kind.JOIN, Kind.MOVE, Kind.RESUME)

    enrollment = models.ForeignKey(
        Enrollment, on_delete=models.PROTECT, related_name='placements',
    )
    course_batch = models.ForeignKey(
        'course.CourseBatch', on_delete=models.PROTECT,
        null=True, blank=True, related_name='placements',
    )
    # The batch the student came from: the old batch on a move, the batch
    # they paused out of on a resume, and the batch they left on a pause or
    # leave.
    from_batch = models.ForeignKey(
        'course.CourseBatch', on_delete=models.PROTECT,
        null=True, blank=True, related_name='placements_out',
    )
    kind = models.CharField(max_length=10, choices=Kind.choices)
    started_on = models.DateField()
    reason = models.TextField()
    # Empty only on rows made by the migration from old data.
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        null=True, blank=True, related_name='placements_made',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'enrollment_placements'
        ordering = ['-created_at', '-id']
        constraints = [
            models.CheckConstraint(
                check=~models.Q(reason=''),
                name='placement_reason_required',
            ),
            models.CheckConstraint(
                check=(
                    models.Q(kind__in=['join', 'move', 'resume'], course_batch__isnull=False)
                    | models.Q(kind__in=['pause', 'leave'], course_batch__isnull=True)
                ),
                name='placement_batch_matches_kind',
            ),
        ]

    def __str__(self):
        return f'{self.get_kind_display()} on enrollment {self.enrollment_id}'

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError('Seat history rows cannot be changed.')
        self.reason = (self.reason or '').strip()
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Seat history rows cannot be deleted.')


class PaymentPlan(models.Model):
    """One set of due dates for an enrollment. Rows are only ever added.

    Changing the due dates adds a new plan with a reason; the newest plan
    counts and the older ones stay as they were. Its parts (Installment rows)
    split the agreed amount.
    """

    enrollment = models.ForeignKey(
        Enrollment, on_delete=models.PROTECT, related_name='payment_plans',
    )
    reason = models.TextField()
    branch = models.ForeignKey(
        'branch.Branch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='payment_plans',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='payment_plans_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'enrollment_payment_plans'
        ordering = ['-created_at', '-id']
        constraints = [
            models.CheckConstraint(
                check=~models.Q(reason=''), name='payment_plan_reason_required',
            ),
        ]

    def __str__(self):
        return f'Payment plan {self.pk} of enrollment {self.enrollment_id}'

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError('Payment plans cannot be changed. Add a new one.')
        self.reason = (self.reason or '').strip()
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Payment plans cannot be deleted.')


class Installment(models.Model):
    """One part of a payment plan: an amount and the day it is due."""

    plan = models.ForeignKey(
        PaymentPlan, on_delete=models.PROTECT, related_name='installments',
    )
    due_on = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        db_table = 'enrollment_installments'
        ordering = ['due_on', 'id']
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount__gt=0), name='installment_amount_positive',
            ),
        ]

    def __str__(self):
        return f'{self.amount} due {self.due_on}'

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError('Due dates cannot be changed. Add a new payment plan.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Due dates cannot be deleted.')


class TeamsAccount(models.Model):
    """A Microsoft Teams sign-in the institute owns. Each one is a licence to give.

    The list of active accounts is the licence total. An account is given to
    one student at a time (a TeamsLicence row); when it is taken back it is
    ready to give again. A retired account (removed from Microsoft 365) is
    kept for the record and no longer counted.
    """

    RETIRE_FIELDS = {'is_active', 'retired_at', 'retired_by', 'retire_reason'}

    email = models.EmailField(unique=True)
    # The branch that keeps it. Empty = any branch may give it.
    branch = models.ForeignKey(
        'branch.Branch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='teams_accounts',
    )
    is_active = models.BooleanField(default=True)
    retired_at = models.DateTimeField(null=True, blank=True)
    retired_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        null=True, blank=True, related_name='+',
    )
    retire_reason = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'teams_accounts'
        ordering = ['email']

    def __str__(self):
        return self.email

    def save(self, *args, **kwargs):
        self.email = (self.email or '').strip().lower()
        super().save(*args, **kwargs)


class TeamsLicence(models.Model):
    """A Microsoft Teams licence given to a student for an enrollment.

    Given and freed only through apps.lead.teams. Freeing needs a reason and
    keeps the row; giving one again adds a new row. One open licence per
    enrollment, and one open licence per Teams account. While the student is
    paused the licence is paused too: still theirs, still counted, back in
    use when they resume.
    """

    # The only fields freeing may set, once.
    FREE_FIELDS = {'released_at', 'released_by', 'release_reason'}
    # Set on pause, cleared on resume. Each change also writes a history row.
    PAUSE_FIELDS = {'paused_at'}
    # Set each time the sign-in details are emailed to the student.
    DETAILS_FIELDS = {'details_sent_at', 'details_sent_to'}

    enrollment = models.ForeignKey(
        Enrollment, on_delete=models.PROTECT, related_name='teams_licences',
    )
    teams_account = models.ForeignKey(
        TeamsAccount, on_delete=models.PROTECT, related_name='licences', null=True,
    )
    # The Teams sign-in given to the student, as it was then.
    account = models.EmailField()
    branch = models.ForeignKey(
        'branch.Branch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='teams_licences',
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='teams_licences_given',
    )
    assigned_at = models.DateTimeField(auto_now_add=True)
    released_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        null=True, blank=True, related_name='teams_licences_freed',
    )
    release_reason = models.TextField(null=True, blank=True)
    # Set while the student is paused.
    paused_at = models.DateTimeField(null=True, blank=True)
    # The last time the sign-in details were emailed, and to whom. The
    # password itself is never stored.
    details_sent_at = models.DateTimeField(null=True, blank=True)
    details_sent_to = models.EmailField(blank=True)

    class Meta:
        db_table = 'teams_licences'
        ordering = ['-assigned_at', '-id']
        constraints = [
            models.UniqueConstraint(
                fields=['enrollment'], condition=models.Q(released_at__isnull=True),
                name='one_open_teams_licence_per_enrollment',
            ),
            models.UniqueConstraint(
                Lower('account'), condition=models.Q(released_at__isnull=True),
                name='teams_account_open_once',
            ),
            models.CheckConstraint(
                check=models.Q(released_at__isnull=True) | (
                    models.Q(release_reason__isnull=False) & ~models.Q(release_reason='')
                ),
                name='teams_licence_free_reason_required',
            ),
        ]

    def __str__(self):
        return f'{self.account} (enrollment {self.enrollment_id})'

    @property
    def is_open(self):
        return self.released_at is None

    def save(self, *args, **kwargs):
        update_fields = kwargs.get('update_fields')
        allowed = self.FREE_FIELDS | self.PAUSE_FIELDS | self.DETAILS_FIELDS
        if self.pk is not None and not (update_fields and set(update_fields) <= allowed):
            raise ValidationError('A Teams licence cannot be changed. Take it back and give a new one.')
        self.account = (self.account or '').strip().lower()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('A Teams licence cannot be deleted. Free it with a reason.')


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
    # Set on payment follow-ups (the call made when a due date is late).
    enrollment = models.ForeignKey(
        Enrollment, on_delete=models.PROTECT,
        null=True, blank=True, related_name='followups',
    )
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

    # Every save or delete updates the lead's next follow-up and last
    # contacted dates, whichever page, job or admin screen made the change.
    def save(self, *args, **kwargs):
        if self.status == self.Status.COMPLETED and self.completed_at is None:
            self.completed_at = timezone.now()
        elif self.status != self.Status.COMPLETED:
            # Reopened (or never done): it has no done time.
            self.completed_at = None
        update_fields = kwargs.get('update_fields')
        if update_fields is not None and 'status' in update_fields:
            kwargs['update_fields'] = {*update_fields, 'completed_at'}
        super().save(*args, **kwargs)
        self.lead.sync_followup_dates()

    def delete(self, *args, **kwargs):
        lead = self.lead
        result = super().delete(*args, **kwargs)
        lead.sync_followup_dates()
        return result


class ReminderConfig(models.Model):
    """Singleton row (pk=1): payment reminder switch and days. Off until turned on."""

    is_active = models.BooleanField(default=False)
    days_before = models.PositiveSmallIntegerField(default=3)
    late_days = models.PositiveSmallIntegerField(default=7)
    call_days = models.PositiveSmallIntegerField(default=15)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'payment_reminder_config'
        verbose_name = 'payment reminder settings'
        verbose_name_plural = 'payment reminder settings'

    def __str__(self):
        return f'Payment reminders ({"on" if self.is_active else "off"})'

    def clean(self):
        if not (self.late_days < self.call_days):
            raise ValidationError('The call follow-up must come after the overdue email.')

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class ReminderLog(models.Model):
    """One payment reminder for one due date of an enrollment.

    Each step is sent once per due date: a row that did not fail blocks
    another of the same step. A failed row stays, and the next run tries
    again with a new row.
    """

    class Step(models.TextChoices):
        BEFORE_DUE = 'before_due', 'Before due date'
        DUE_TODAY = 'due_today', 'Due today'
        LATE = 'late', 'Overdue'
        CALL = 'call', 'Call follow-up'

    class Channel(models.TextChoices):
        EMAIL = 'email', 'Email'
        SMS = 'sms', 'SMS'
        FOLLOW_UP = 'follow_up', 'Follow-up'

    class Status(models.TextChoices):
        SENDING = 'sending', 'Sending'
        SENT = 'sent', 'Sent'
        FAILED = 'failed', 'Failed'
        SKIPPED = 'skipped', 'Skipped'

    enrollment = models.ForeignKey(
        Enrollment, on_delete=models.PROTECT, related_name='reminders',
    )
    due_on = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    step = models.CharField(max_length=12, choices=Step.choices)
    channel = models.CharField(max_length=10, choices=Channel.choices)
    sent_to = models.CharField(max_length=255, blank=True, default='')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SENDING)
    error = models.TextField(blank=True, default='')
    followup = models.ForeignKey(
        'lead.FollowUp', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    branch = models.ForeignKey(
        'branch.Branch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='reminder_logs',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'payment_reminder_logs'
        ordering = ['-created_at', '-id']
        constraints = [
            models.UniqueConstraint(
                fields=['enrollment', 'due_on', 'step'],
                condition=~models.Q(status='failed'),
                name='reminder_once_per_due_date_and_step',
            ),
        ]

    def __str__(self):
        return f'{self.get_step_display()} for enrollment {self.enrollment_id} ({self.due_on})'


FILE_ADDRESS = '/api/files/'


def validate_file_address(value):
    """A stored file: the /api/files/<key> address /api/upload/ gives, or a web address."""
    if value.startswith(FILE_ADDRESS) and len(value) > len(FILE_ADDRESS):
        return
    URLValidator(
        schemes=['http', 'https'],
        message='Enter the address the upload gave, or a web address starting with https://.',
    )(value)


class LeadDocument(models.Model):
    lead = models.ForeignKey(
        Lead, on_delete=models.CASCADE, related_name='documents',
    )
    document_type = models.CharField(max_length=255)
    branch = models.ForeignKey(
        'branch.Branch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='lead_documents',
    )
    file = models.CharField(max_length=500, validators=[validate_file_address])
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


class VoiceNote(models.Model):
    """A short recording a counselor makes on an enquiry.

    The file is private: it is kept under `file_key` (in R2, or the local
    media folder when R2 is not set up) and only the CRM plays it, to users
    who can see the lead. Deleting the row removes the file (see voice_notes.py).
    """

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='voice_notes')
    enquiry = models.ForeignKey(
        LeadCourseInterest, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='voice_notes',
    )
    file_key = models.CharField(max_length=255, editable=False)
    content_type = models.CharField(max_length=50)
    size_bytes = models.PositiveIntegerField()
    duration_seconds = models.PositiveIntegerField(default=0)
    note = models.CharField(max_length=255, blank=True, default='')
    branch = models.ForeignKey(
        'branch.Branch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='voice_notes',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='voice_notes_recorded',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'lead_voice_notes'
        ordering = ['-created_at']

    def __str__(self):
        return f'Voice note {self.pk} - {self.lead.full_name}'


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
