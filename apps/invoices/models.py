from datetime import datetime

from django.conf import settings
from django.db import models


class Invoice(models.Model):
    class Type(models.TextChoices):
        ENROLLMENT = 'enrollment', 'Enrollment (Course Fee)'
        PAYMENT = 'payment', 'Payment Receipt'

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        CANCELLED = 'cancelled', 'Cancelled'

    invoice_number = models.CharField(max_length=20, unique=True, editable=False)
    enrollment = models.ForeignKey(
        'lead.Enrollment', on_delete=models.CASCADE, related_name='invoices',
    )
    student = models.ForeignKey(
        'student.Student', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='invoices',
    )
    payment = models.ForeignKey(
        'lead.Payment', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='invoices',
    )
    type = models.CharField(max_length=12, choices=Type.choices)
    # An invoice is never edited or deleted: a wrong one is cancelled with a reason.
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    cancel_reason = models.TextField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        null=True, blank=True, related_name='invoices_cancelled',
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    course_name = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    total_paid_so_far = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    remaining_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    branch = models.ForeignKey(
        'branch.Branch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='invoices',
    )
    emailed_to = models.EmailField(null=True, blank=True)
    cc_emails = models.JSONField(default=list, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='invoices_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'invoices'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.invoice_number} ({self.type})'

    def save(self, *args, **kwargs):
        if not self.invoice_number:
            year = datetime.now().year
            prefix = f'INV-{year}'
            last = Invoice.objects.filter(
                invoice_number__startswith=prefix,
            ).order_by('invoice_number').last()
            seq = 1
            if last:
                seq = int(last.invoice_number.split('-')[-1]) + 1
            self.invoice_number = f'{prefix}-{seq:06d}'
        super().save(*args, **kwargs)


class InvoiceEmailConfig(models.Model):
    """Singleton row (pk=1) holding global invoice email configuration."""
    cc_emails = models.JSONField(default=list, blank=True)
    subject_prefix = models.CharField(max_length=100, default='Skill Shikshya')
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'invoice_email_config'

    def __str__(self):
        return f'Invoice Email Config (cc={len(self.cc_emails)})'

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)