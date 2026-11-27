"""Invoices and receipts as standard A4 PDFs.

One HTML template (invoices/pdf/document.html) is turned into a PDF with
WeasyPrint. The PDF uses the numbers kept on the invoice when it was made, so
it reads the same every time it is opened or sent again.
"""
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from django.template.loader import render_to_string

NEPAL = ZoneInfo('Asia/Kathmandu')

_ONES = (
    'Zero One Two Three Four Five Six Seven Eight Nine Ten Eleven Twelve Thirteen '
    'Fourteen Fifteen Sixteen Seventeen Eighteen Nineteen'
).split()
_TENS = 'Zero Ten Twenty Thirty Forty Fifty Sixty Seventy Eighty Ninety'.split()


def _below_hundred(n):
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    return _TENS[tens] + (f'-{_ONES[ones]}' if ones else '')


def _below_thousand(n):
    hundreds, rest = divmod(n, 100)
    words = []
    if hundreds:
        words.append(f'{_ONES[hundreds]} Hundred')
    if rest:
        words.append(_below_hundred(rest))
    return ' '.join(words)


def number_in_words(n):
    """Nepali/Indian grouping: 1,25,000 is 'One Lakh Twenty-Five Thousand'."""
    if n == 0:
        return 'Zero'
    words = []
    crore, n = divmod(n, 10_000_000)
    lakh, n = divmod(n, 100_000)
    thousand, n = divmod(n, 1000)
    if crore:
        words.append(f'{number_in_words(crore)} Crore')
    if lakh:
        words.append(f'{_below_hundred(lakh)} Lakh')
    if thousand:
        words.append(f'{_below_hundred(thousand)} Thousand')
    if n:
        words.append(_below_thousand(n))
    return ' '.join(words)


def amount_in_words(amount):
    """'Rupees Twenty Thousand Five Hundred and Fifty Paisa Only'."""
    amount = Decimal(amount or 0).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    rupees = int(amount)
    paisa = int((amount - rupees) * 100)
    text = f'Rupees {number_in_words(rupees)}'
    if paisa:
        text += f' and {_below_hundred(paisa)} Paisa'
    return f'{text} Only'


def money(amount):
    amount = Decimal(amount or 0)
    return f'{amount:,.2f}'


def _batch(enrollment):
    batch = enrollment.batch or enrollment.current_batch
    if batch is None:
        return ''
    shift = batch.batch
    if shift and shift.start_time and shift.end_time:
        times = f"{shift.start_time.strftime('%-I:%M %p')} – {shift.end_time.strftime('%-I:%M %p')}"
        return f'{batch.name} ({times})'
    return batch.name


def document_context(invoice):
    from apps.global_config.mailer import organisation

    enrollment = invoice.enrollment
    student = invoice.student or enrollment.student
    lead = enrollment.lead
    payment = invoice.payment
    is_receipt = invoice.type == 'payment'
    discount = Decimal(enrollment.standard_discount or 0) + Decimal(enrollment.additional_discount or 0)

    if is_receipt:
        lines = [{'text': f'Payment for {invoice.course_name}', 'amount': money(invoice.amount)}]
        total = Decimal(invoice.amount)
    else:
        lines = [{'text': f'Course fee: {invoice.course_name}', 'amount': money(enrollment.listed_fee)}]
        if discount:
            lines.append({'text': 'Discount', 'amount': f'-{money(discount)}'})
        total = Decimal(invoice.amount)

    plan = []
    if not is_receipt:
        from apps.lead.services import current_plan

        current = current_plan(enrollment)
        if current:
            plan = [
                {'due_on': part.due_on.strftime('%-d %b %Y') if part.due_on else '', 'amount': money(part.amount)}
                for part in current.installments.all()
            ]

    branch = enrollment.branch
    return {
        'org': organisation(),
        'invoice': invoice,
        'is_receipt': is_receipt,
        'title': 'Payment Receipt' if is_receipt else 'Invoice',
        'cancelled': invoice.status == 'cancelled',
        'issued_on': invoice.created_at.astimezone(NEPAL).strftime('%-d %b %Y'),
        'branch': branch,
        'bill_to': {
            'name': student.full_name if student else lead.full_name,
            'student_id': student.student_id if student else '',
            'phone': (student.phone if student else lead.phone) or '',
            'email': (student.email if student else lead.email) or '',
            'address': (student.address if student else lead.address) or '',
        },
        'course': invoice.course_name,
        'batch': _batch(enrollment),
        'lines': lines,
        'total': money(total),
        'total_words': amount_in_words(total),
        'paid_so_far': money(invoice.total_paid_so_far),
        'balance': money(invoice.remaining_balance),
        'plan': plan,
        'payment': {
            'method': payment.get_payment_method_display(),
            'reference': payment.transaction_reference or '',
            'paid_on': payment.payment_date.astimezone(NEPAL).strftime('%-d %b %Y, %-I:%M %p'),
            'received_by': payment.received_by.name if payment.received_by_id else '',
        } if payment else None,
    }


def document_html(invoice):
    return render_to_string('invoices/pdf/document.html', document_context(invoice))


def invoice_pdf(invoice):
    """The invoice or receipt as PDF bytes."""
    from weasyprint import HTML

    return HTML(string=document_html(invoice)).write_pdf()
