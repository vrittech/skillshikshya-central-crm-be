from datetime import timedelta
from zoneinfo import ZoneInfo

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import timezone

NEPAL_TZ = ZoneInfo('Asia/Kathmandu')


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
