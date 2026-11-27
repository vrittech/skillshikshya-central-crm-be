from celery import shared_task


@shared_task(ignore_result=True)
def send_invoice_email_task(invoice_id):
    """Kept for jobs queued before the new emails. Sends the invoice again."""
    from apps.invoices.models import Invoice
    from apps.lead.emails import invoice_again

    invoice = Invoice.objects.select_related('enrollment__lead', 'student').filter(pk=invoice_id).first()
    if invoice is not None and invoice.status == Invoice.Status.ACTIVE:
        invoice_again(invoice)
