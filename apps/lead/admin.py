from django.contrib import admin

from apps.lead.models import (
    LearningMode, Institution, Province, City, LeadSource, Campaign, LeadStatus, LeadPriority,
    LeadTag, LeadType, Lead, LeadCourseInterest, Enrollment, Payment, Adjustment,
    EnrollmentHistory, LeadCall, LeadActivity, FollowUp, LeadDocument, Notification, Placement,
    PaymentPlan, Installment, ReminderConfig, ReminderLog, TeamsAccount, TeamsLicence,
    EnquiryStageChange, VoiceNote, LeadChange,
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

    def save_model(self, request, obj, form, change):
        obj._changed_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(Enrollment)
class EnrollmentAdmin(ReadOnlyAdmin):
    list_display = [
        'id', 'lead', 'course', 'batch', 'current_batch', 'status', 'agreed_amount',
        'branch', 'created_at',
    ]
    list_filter = ['status', 'branch']
    search_fields = ['lead__full_name', 'lead__phone', 'course__name']
    list_select_related = ['lead', 'course', 'batch', 'current_batch', 'branch']


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


@admin.register(Placement)
class PlacementAdmin(ReadOnlyAdmin):
    list_display = [
        'id', 'enrollment', 'kind', 'course_batch', 'from_batch', 'started_on', 'reason',
        'created_by', 'created_at',
    ]
    list_filter = ['kind']
    search_fields = ['enrollment__lead__full_name', 'reason']
    list_select_related = ['enrollment__lead', 'course_batch', 'from_batch', 'created_by']


class InstallmentInline(admin.TabularInline):
    model = Installment
    fields = ['due_on', 'amount']
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(PaymentPlan)
class PaymentPlanAdmin(ReadOnlyAdmin):
    list_display = ['id', 'enrollment', 'reason', 'created_by', 'created_at']
    search_fields = ['enrollment__lead__full_name', 'reason']
    list_select_related = ['enrollment__lead', 'created_by']
    inlines = [InstallmentInline]


@admin.register(ReminderConfig)
class ReminderConfigAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'is_active', 'days_before', 'late_days', 'call_days', 'updated_at']

    def has_add_permission(self, request):
        return not ReminderConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ReminderLog)
class ReminderLogAdmin(ReadOnlyAdmin):
    list_display = [
        'id', 'enrollment', 'due_on', 'amount', 'step', 'channel', 'sent_to', 'status', 'created_at',
    ]
    list_filter = ['step', 'channel', 'status']
    search_fields = ['enrollment__lead__full_name', 'sent_to']
    list_select_related = ['enrollment__lead']


@admin.register(TeamsAccount)
class TeamsAccountAdmin(admin.ModelAdmin):
    list_display = ('email', 'branch', 'is_active', 'created_at')
    list_filter = ('is_active', 'branch')
    search_fields = ('email',)
    readonly_fields = ('retired_at', 'retired_by', 'created_by', 'created_at')

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(TeamsLicence)
class TeamsLicenceAdmin(ReadOnlyAdmin):
    list_display = ['account', 'enrollment', 'assigned_by', 'assigned_at', 'released_at']
    search_fields = ['account', 'enrollment__lead__full_name']
    list_select_related = ['enrollment__lead', 'enrollment__course', 'assigned_by']


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
admin.site.register(LeadCall)
admin.site.register(LeadActivity)
admin.site.register(FollowUp)
admin.site.register(LeadDocument)
admin.site.register(Notification)


@admin.register(LeadCourseInterest)
class EnquiryAdmin(admin.ModelAdmin):
    """The stage changes only in the app, so it is shown here but not edited."""

    list_display = ['lead', 'course', 'stage', 'counselor', 'source', 'branch', 'created_at']
    list_filter = ['stage', 'lost_reason', 'branch']
    search_fields = ['lead__full_name', 'lead__phone', 'course__name', 'course_name']
    readonly_fields = [
        'stage', 'stage_changed_at', 'lost_reason', 'lost_note', 'lost_at_stage', 'enrollment',
    ]


@admin.register(EnquiryStageChange)
class EnquiryStageChangeAdmin(ReadOnlyAdmin):
    list_display = ['enquiry', 'from_stage', 'to_stage', 'changed_by', 'created_at']
    list_filter = ['to_stage']


@admin.register(VoiceNote)
class VoiceNoteAdmin(ReadOnlyAdmin):
    """Look only. Deleting through the app also removes the recording."""

    list_display = ['id', 'lead', 'enquiry', 'duration_seconds', 'created_by', 'created_at']
    list_select_related = ['lead', 'enquiry', 'created_by']


@admin.register(LeadChange)
class LeadChangeAdmin(ReadOnlyAdmin):
    list_display = ['lead', 'field', 'from_value', 'to_value', 'changed_by', 'created_at']
    list_filter = ['field']
