from rest_framework import serializers

from apps.invoices.models import Invoice, InvoiceEmailConfig


class InvoiceSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source='student.full_name', read_only=True)
    enrollment_id = serializers.IntegerField(source='enrollment.id', read_only=True)
    payment_id = serializers.IntegerField(source='payment.id', read_only=True)
    type_display = serializers.CharField(source='get_type_display', read_only=True)
    branch_name = serializers.CharField(source='branch.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    cancelled_by_name = serializers.CharField(source='cancelled_by.name', read_only=True)

    class Meta:
        model = Invoice
        fields = [
            'id', 'invoice_number', 'type', 'type_display',
            'enrollment', 'enrollment_id',
            'student', 'student_name',
            'payment', 'payment_id',
            'course_name', 'amount',
            'total_paid_so_far', 'remaining_balance',
            'branch', 'branch_name',
            'emailed_to', 'cc_emails', 'sent_at',
            'status', 'status_display',
            'cancel_reason', 'cancelled_by', 'cancelled_by_name', 'cancelled_at',
            'created_at',
        ]
        read_only_fields = fields


class InvoiceEmailConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceEmailConfig
        fields = ['cc_emails', 'subject_prefix', 'is_active', 'updated_at']
        read_only_fields = ['updated_at']