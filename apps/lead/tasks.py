import logging
from datetime import timedelta
from zoneinfo import ZoneInfo

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import timezone

NEPAL_TZ = ZoneInfo('Asia/Kathmandu')
logger = logging.getLogger(__name__)


@shared_task
def send_counselor_assignment_email_task(counselor_name, counselor_email, reference_number, full_name, phone, email, lead_url):
    html_message = render_to_string('lead/emails/counselor_assignment_email.html', {
        'counselor_name': counselor_name,
        'reference_number': reference_number,
        'full_name': full_name,
        'phone': phone,
        'email': email,
        'lead_url': lead_url,
    })
    send_mail(
        'New Lead Assigned - Skill Shikshya CRM',
        f'Lead {reference_number} ({full_name}) has been assigned to you. View: {lead_url}',
        settings.DEFAULT_FROM_EMAIL,
        [counselor_email],
        html_message=html_message,
        fail_silently=False,
    )


@shared_task
def send_followup_reminders_task():
    from apps.lead.models import FollowUp
    from apps.lead.models import Notification

    now = timezone.now()
    window_start = now - timedelta(minutes=5)

    followups = FollowUp.objects.filter(
        status='pending',
        reminder_at__gte=window_start,
        reminder_at__lte=now,
        reminder_sent=False,
    ).select_related('lead', 'assigned_to', 'lead__assigned_counselor')

    for fup in followups:
        user = fup.assigned_to or fup.lead.assigned_counselor
        if not user:
            continue

        Notification.objects.create(
            recipient=user,
            title='Follow-up Reminder',
            message=(
                f'Follow-up for {fup.lead.full_name} '
                f'({fup.lead.reference_number}) is scheduled at '
                f'{fup.scheduled_at.astimezone(NEPAL_TZ).strftime("%Y-%m-%d %H:%M")} (Nepal Time).'
            ),
            link=f'/leads/{fup.lead_id}',
        )

        if user.email:
            frontend_url = settings.FRONTEND_URL.rstrip('/')
            lead_url = f'{frontend_url}/leads/{fup.lead_id}'
            local_scheduled = fup.scheduled_at.astimezone(NEPAL_TZ).strftime('%Y-%m-%d %H:%M')
            html_message = render_to_string('lead/emails/followup_reminder_email.html', {
                'user_name': user.name,
                'lead_name': fup.lead.full_name,
                'reference_number': fup.lead.reference_number,
                'scheduled_at': local_scheduled,
                'purpose': fup.purpose or '—',
                'lead_url': lead_url,
            })
            send_mail(
                'Follow-up Reminder - Skill Shikshya CRM',
                f'Reminder: Follow-up with {fup.lead.full_name} ({fup.lead.reference_number}) at {fup.scheduled_at}.',
                settings.DEFAULT_FROM_EMAIL,
                [user.email],
                html_message=html_message,
                fail_silently=False,
            )

        FollowUp.objects.filter(pk=fup.pk).update(reminder_sent=True)


@shared_task
def send_payment_reminders_task():
    """Daily at 09:00 Nepal time. Does nothing while payment reminders are off."""
    from apps.lead.models import ReminderConfig
    from apps.lead.reminders import send_payment_reminders

    config = ReminderConfig.load()
    if not config.is_active:
        return 0
    return len(send_payment_reminders(config=config))


@shared_task(
    autoretry_for=(Exception,),
    retry_backoff=30,        # wait 30s, then 60s, 120s, ... between tries
    retry_backoff_max=3600,  # never wait more than an hour
    retry_jitter=True,       # spread the waits so retries don't all land at once
    max_retries=8,           # about 2 hours of trying in all
    acks_late=True,          # if the worker dies mid-job, Redis hands the job out again
    ignore_result=True,      # nothing to read back, so don't store a result in Redis
)
def delete_voice_note_file_task(key):
    """Remove a deleted voice note's recording from R2 (or the local media folder).

    Queued by apps.lead.voice_notes once the delete is saved. Deleting a file
    that is already gone is not an error, so running this twice is harmless:
    that is what makes the retries and acks_late safe.
    """
    from apps.lead.voice_notes import delete_file

    delete_file(key)


@shared_task(ignore_result=True)
def sweep_voice_note_files_task():
    """Nightly (beat): delete recordings no voice note points to. See apps.lead.voice_sweep.

    No retries: if storage is down tonight, tomorrow night's run picks it up.
    """
    from apps.lead.voice_sweep import sweep_orphan_files

    result = sweep_orphan_files()
    logger.info('Voice note sweep: %s', result.summary())
    return result.summary()
