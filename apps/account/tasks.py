from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail

from apps.global_config.mailer import render_email


@shared_task
def send_reset_link_email_task(email, reset_link):
    html_message, _ = render_email('account/emails/reset_link_email.html', {'reset_link': reset_link})
    send_mail(
        'Password Reset - Skill Shikshya CRM',
        f'Click the link to reset your password: {reset_link}',
        settings.DEFAULT_FROM_EMAIL,
        [email],
        html_message=html_message,
        fail_silently=False,
    )


@shared_task
def send_password_changed_email_task(email, name):
    html_message, _ = render_email('account/emails/password_changed_email.html', {'name': name})
    send_mail(
        'Password Changed - Skill Shikshya CRM',
        f'Hi {name}, your Skill Shikshya CRM password has been changed.',
        settings.DEFAULT_FROM_EMAIL,
        [email],
        html_message=html_message,
        fail_silently=False,
    )
