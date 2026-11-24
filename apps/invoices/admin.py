from django.contrib import admin

from apps.invoices.models import Invoice, InvoiceEmailConfig
from apps.lead.admin import ReadOnlyAdmin


@admin.register(Invoice)
class InvoiceAdmin(ReadOnlyAdmin):
    """View only. A wrong invoice is cancelled in the app, with a reason."""

    list_display = (
        'invoice_number', 'type', 'status', 'course_name', 'amount',
        'total_paid_so_far', 'remaining_balance', 'emailed_to', 'sent_at',
    )
    list_filter = ('type', 'status')
    search_fields = ('invoice_number', 'course_name', 'emailed_to')


admin.site.register(InvoiceEmailConfig)