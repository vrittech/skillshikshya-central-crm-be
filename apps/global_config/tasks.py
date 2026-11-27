import logging

from celery import shared_task

logger = logging.getLogger(__name__)

RETRIES = 5


def _no_longer_needed(log):
    """Why a queued email should not go out any more, or ''."""
    from apps.global_config.models import EmailLog
    from apps.lead.models import Enrollment

    if log.kind == EmailLog.Kind.ENROLLMENT_PENDING and log.enrollment_id:
        if log.enrollment.status != Enrollment.Status.PENDING:
            return 'The enrollment was confirmed or cancelled before this was sent.'
    return ''


@shared_task(bind=True, max_retries=RETRIES, acks_late=True, ignore_result=True)
def send_email_task(self, log_id):
    """Send one logged email. A refused send is tried again later, then marked failed."""
    from apps.global_config.mailer import deliver, error_text
    from apps.global_config.models import EmailLog

    log = EmailLog.objects.select_related('enrollment', 'invoice', 'branch').filter(pk=log_id).first()
    if log is None or log.status != EmailLog.Status.QUEUED:
        return
    reason = _no_longer_needed(log)
    if reason:
        log.status = EmailLog.Status.SKIPPED
        log.error = reason
        log.save(update_fields=['status', 'error'])
        return
    try:
        deliver(log)
    except Exception as exc:  # noqa: BLE001 - kept on the row, then retried
        log.error = error_text(exc)
        if self.request.retries >= RETRIES:
            log.status = EmailLog.Status.FAILED
            log.save(update_fields=['status', 'error'])
            logger.error('Email %s to %s failed for good: %s', log.id, log.to_email, exc)
            return
        log.save(update_fields=['error'])
        raise self.retry(exc=exc, countdown=60 * 2 ** self.request.retries)
