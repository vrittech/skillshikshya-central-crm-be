from django.contrib import admin

from apps.lead.models import (
    LearningMode, Institution, Province, City, LeadSource, Campaign, LeadStatus, LeadPriority,
    LeadTag, LeadType, Lead, LeadCourseInterest, Enrollment, Payment, Adjustment,
    EnrollmentHistory, LeadCall, LeadActivity, FollowUp, LeadDocument, Notification,
)


class ReadOnlyAdmin(admin.ModelAdmin):
    """View-only admin for money records.

    Money is changed only through the app, which asks for a reason and writes
    the history row. The admin can look, but not add, edit or delete.
    """

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class LeadAdmin(admin.ModelAdmin):
    list_display = [
        'reference_number', 'full_name', 'phone', 'email', 'lead_type', 'source',
        'status', 'priority', 'assigned_counselor', 'is_converted', 'is_archived', 'is_deleted', 'created_at',
    ]
    list_filter = ['lead_type', 'source', 'status', 'priority', 'is_converted', 'is_archived', 'is_deleted']
    search_fields = ['full_name', 'reference_number', 'phone', 'email']


@admin.register(Enrollment)
class EnrollmentAdmin(ReadOnlyAdmin):
    list_display = ['id', 'lead', 'course', 'batch', 'status', 'agreed_amount', 'branch', 'created_at']
    list_filter = ['status', 'branch']
    search_fields = ['lead__full_name', 'lead__phone', 'course__name']
    list_select_related = ['lead', 'course', 'batch', 'branch']


@admin.register(Payment)
class PaymentAdmin(ReadOnlyAdmin):
    list_display = ['id', 'enrollment', 'amount', 'payment_method', 'status', 'payment_date', 'received_by']
    list_filter = ['status', 'payment_method']
    search_fields = ['enrollment__lead__full_name', 'transaction_reference']
    list_select_related = ['enrollment__lead', 'received_by']


@admin.register(Adjustment)
class AdjustmentAdmin(ReadOnlyAdmin):
    list_display = ['id', 'enrollment', 'kind', 'amount', 'reason', 'created_by', 'created_at']
    list_filter = ['kind']
    search_fields = ['enrollment__lead__full_name', 'reason']
    list_select_related = ['enrollment__lead', 'created_by']


@admin.register(EnrollmentHistory)
class EnrollmentHistoryAdmin(ReadOnlyAdmin):
    list_display = ['id', 'enrollment', 'action', 'reason', 'performed_by', 'created_at']
    list_filter = ['action']
    search_fields = ['enrollment__lead__full_name', 'reason']
    list_select_related = ['enrollment__lead', 'performed_by']


admin.site.register(LearningMode)
admin.site.register(Institution)
admin.site.register(Province)
admin.site.register(City)
admin.site.register(LeadSource)
admin.site.register(Campaign)
admin.site.register(LeadStatus)
admin.site.register(LeadPriority)
admin.site.register(LeadTag)
admin.site.register(LeadType)
admin.site.register(Lead, LeadAdmin)
admin.site.register(LeadCourseInterest)
admin.site.register(LeadCall)
admin.site.register(LeadActivity)
admin.site.register(FollowUp)
admin.site.register(LeadDocument)
admin.site.register(Notification)
