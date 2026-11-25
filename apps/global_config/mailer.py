"""Emails the CRM sends to leads, students and counselors.

queue_email() renders the email now, keeps it in an EmailLog row and lets the
worker send it once the transaction commits (with retries). send_now() sends
straight away and is used for Teams sign-in details, so a password never waits
in the queue or sits in the log.

Every email uses the shared layout in global_config/templates/emails/base.html
with the organisation details from the Settings page. An invoice or receipt
linked to the row goes with it as a PDF.
"""
import logging
import re
from dataclasses import dataclass
from email.utils import formataddr, parseaddr
from html import unescape

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from apps.global_config.models import EmailLog, OrganisationSettings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Organisation:
    name: str
    address: str
    phone: str
    email: str
    website: str
    pan_number: str
    logo_url: str
    receipt_note: str


def default_organisation():
    return Organisation(
        name='Skill Shikshya Pvt. Ltd.',
        address='Sankhamul, Kathmandu',
        phone='',
        email='',
        website='skillshikshya.com',
        pan_number='',
        logo_url=getattr(settings, 'INVOICE_LOGO_URL', ''),
        receipt_note='',
    )


def organisation():
    """The organisation details saved on the Settings page, else the defaults."""
    default = default_organisation()
    row = OrganisationSettings.load()
    if row is None:
        return default
    return Organisation(
        name=row.name or default.name,
        address=row.address,
        phone=row.phone,
        email=row.email,
        website=row.website,
        pan_number=row.pan_number,
        logo_url=row.logo_url or default.logo_url,
        receipt_note=row.receipt_note,
    )


_DROP = re.compile(r'<(head|style|script)[^>]*>.*?</\1>', re.S | re.I)
_LINK = re.compile(r'<a\s[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I)
_BREAK = re.compile(r'<br\s*/?>|</(p|div|tr|h[1-6]|li|table)>', re.I)
_CELL = re.compile(r'</t[dh]>', re.I)
_TAG = re.compile(r'<[^>]+>')


def html_to_text(html):
    """A plain-text copy of an email, for mail apps that don't show HTML."""
    text = re.sub(r'\s+', ' ', _DROP.sub('', html))
    text = _LINK.sub(
        lambda m: m.group(2) if m.group(1).startswith('mailto:') else f'{m.group(2)} ({m.group(1)})',
        text,
    )
    text = _CELL.sub(' ', _BREAK.sub('\n', text))
    text = unescape(_TAG.sub('', text))
    lines = [re.sub(r'[ \t\xa0]+', ' ', line).strip() for line in text.splitlines()]
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip() + '\n'


def render_email(template, context):
    """(html, text) for a template that extends emails/base.html."""
    html = render_to_string(template, {
        'org': organisation(),
        'frontend_url': (getattr(settings, 'FRONTEND_URL', '') or '').rstrip('/'),
        **context,
    })
    return html, html_to_text(html)


def _from_address(org):
    """The From address in use, shown under the organisation's name."""
    name, address = parseaddr(settings.DEFAULT_FROM_EMAIL)
    return formataddr((name or org.name, address))


def _reply_to(log, org):
    branch_email = getattr(log.branch, 'email', '') if log.branch_id else ''
    reply = branch_email or org.email
    return [reply] if reply else []


def _message(log, html, text):
    org = organisation()
    message = EmailMultiAlternatives(
        subject=log.subject,
        body=text,
        from_email=_from_address(org),
        to=[log.to_email],
        cc=log.cc or [],
        reply_to=_reply_to(log, org),
    )
    message.attach_alternative(html, 'text/html')
    if log.invoice_id:
        from apps.invoices.pdf import invoice_pdf

        message.attach(f'{log.invoice.invoice_number}.pdf', invoice_pdf(log.invoice), 'application/pdf')
    return message


def _mark_sent(log):
    log.status = EmailLog.Status.SENT
    log.sent_at = timezone.now()
    log.error = ''
    log.save(update_fields=['status', 'sent_at', 'error'])
    if log.invoice_id:
        invoice = log.invoice
        invoice.emailed_to = log.to_email
        invoice.cc_emails = log.cc or []
        invoice.sent_at = log.sent_at
        invoice.save(update_fields=['emailed_to', 'cc_emails', 'sent_at'])


def error_text(exc):
    from apps.global_config.integrations import email_config, email_error_message

    return email_error_message(exc, email_config())


def deliver(log, *, html=None, text=None):
    """Send the logged email now. Raises when the server refuses it."""
    _message(log, html or log.body_html, text or log.body_text).send(fail_silently=False)
    _mark_sent(log)


def _skipped(kind, subject, *, reason, **links):
    return EmailLog.objects.create(
        kind=kind, status=EmailLog.Status.SKIPPED, to_email='', subject=subject,
        body_html='', body_text='', error=reason, **links,
    )


def queue_email(kind, *, to, subject, template, context, lead=None, enrollment=None,
                invoice=None, cc=(), branch=None, user=None, countdown=None):
    """Render and log an email, and send it from the worker after commit.

    With no address, a "not sent" row is kept so staff can see why.
    """
    from apps.global_config.tasks import send_email_task

    links = {
        'lead': lead, 'enrollment': enrollment, 'invoice': invoice,
        'branch': branch, 'created_by': user,
    }
    if not to:
        return _skipped(kind, subject, reason='No email address.', **links)
    html, text = render_email(template, context)
    log = EmailLog.objects.create(
        kind=kind, to_email=to, cc=[c for c in cc if c and c != to], subject=subject,
        body_html=html, body_text=text, **links,
    )
    log_id = log.id
    transaction.on_commit(lambda: send_email_task.apply_async((log_id,), countdown=countdown))
    return log


def send_now(kind, *, to, subject, template, context, hidden_context, lead=None,
             enrollment=None, branch=None, user=None):
    """Send right away and keep the log with `hidden_context` in place of secrets.

    Returns the log row; its status says whether the email went.
    """
    links = {'lead': lead, 'enrollment': enrollment, 'branch': branch, 'created_by': user}
    if not to:
        return _skipped(kind, subject, reason='No email address.', **links)
    html, text = render_email(template, context)
    kept_html, kept_text = render_email(template, {**context, **hidden_context})
    log = EmailLog.objects.create(
        kind=kind, to_email=to, subject=subject, body_html=kept_html, body_text=kept_text, **links,
    )
    try:
        deliver(log, html=html, text=text)
    except Exception as exc:  # noqa: BLE001 - the reason is shown to staff
        logger.warning('Email %s to %s failed: %s', log.id, to, exc)
        log.status = EmailLog.Status.FAILED
        log.error = error_text(exc)
        log.save(update_fields=['status', 'error'])
    return log
