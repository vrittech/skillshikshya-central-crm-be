"""Daily payment reminders.

Every morning each unpaid due date is checked once. It gets the step it has
reached: an email a few days before, an email from the due day, an overdue
email, and in the end a call follow-up for the counselor. Each step goes out
once per due date (see ReminderLog). Reminders are off until
ReminderConfig.is_active is turned on.
"""
import logging
from collections import defaultdict
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.db.models import Prefetch, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.global_config.mailer import render_email
from apps.lead.models import (
    Enrollment, FollowUp, Placement, PaymentPlan, ReminderConfig, ReminderLog,
)
from apps.lead.services import due_rows

logger = logging.getLogger(__name__)

NEPAL_TZ = ZoneInfo('Asia/Kathmandu')
Step = ReminderLog.Step

# Most serious first: the subject of a combined email follows the worst step.
STEP_ORDER = [Step.CALL, Step.LATE, Step.DUE_TODAY, Step.BEFORE_DUE]
SUBJECTS = {
    Step.BEFORE_DUE: 'Payment reminder',
    Step.DUE_TODAY: 'Payment due',
    Step.LATE: 'Payment overdue',
}


def step_for(days_late, config):
    """The step a due date has reached, or None. days_late < 0 means not due yet."""
    if days_late >= config.call_days:
        return Step.CALL
    if days_late >= config.late_days:
        return Step.LATE
    if days_late >= 0:
        return Step.DUE_TODAY
    if -days_late <= config.days_before:
        return Step.BEFORE_DUE
    return None


def _enrollments():
    """Enrollments that can owe money and have due dates, ready for due_rows()."""
    return (
        Enrollment.objects.filter(
            status__in=Enrollment.SEAT_STATUSES, payment_plans__isnull=False,
        )
        .distinct()
        .select_related('lead', 'lead__assigned_counselor', 'student', 'course', 'branch')
        .annotate(paid_total=Coalesce(
            Sum('payments__amount', filter=Q(payments__status='successful')),
            Decimal('0'),
        ))
        .prefetch_related(
            'adjustments',
            Prefetch('payment_plans', queryset=PaymentPlan.objects.prefetch_related('installments')),
            Prefetch('placements', queryset=Placement.objects.order_by('-created_at', '-id')),
            Prefetch(
                'reminders',
                queryset=ReminderLog.objects.exclude(status=ReminderLog.Status.FAILED),
                to_attr='done_reminders',
            ),
        )
    )


def _is_paused(enrollment):
    if enrollment.current_batch_id:
        return False
    placements = list(enrollment.placements.all())
    return bool(placements) and placements[0].kind == Placement.Kind.PAUSE


def plan_reminders(enrollment, *, today, config):
    """What this enrollment should get today: a list of {due_on, amount, days_late, step}."""
    if _is_paused(enrollment):
        return []
    done = {(r.due_on, r.step) for r in enrollment.done_reminders}
    left_by_day = defaultdict(Decimal)
    for row in due_rows(enrollment, today=today, paid=enrollment.paid_total):
        if row['left'] > 0 and row['due_on'] is not None:
            left_by_day[row['due_on']] += row['left']

    due = []
    for day, amount in sorted(left_by_day.items()):
        days_late = (today - day).days
        step = step_for(days_late, config)
        if step and (day, step) not in done:
            due.append({'due_on': day, 'amount': amount, 'days_late': days_late, 'step': step})
    return due


def _reserve(enrollment, item, *, channel, sent_to):
    """Write the log row first, so two runs can't send the same step twice."""
    try:
        with transaction.atomic():
            return ReminderLog.objects.create(
                enrollment=enrollment,
                due_on=item['due_on'],
                amount=item['amount'],
                step=item['step'],
                channel=channel,
                sent_to=sent_to,
                branch=enrollment.branch,
            )
    except IntegrityError:
        return None


def _finish(logs, status, *, error='', followup=None):
    ReminderLog.objects.filter(pk__in=[log.pk for log in logs]).update(
        status=status, error=error, followup=followup,
    )


def _recipient(enrollment):
    student = enrollment.student
    return (student.email if student and student.email else None) or enrollment.lead.email


def _days(n):
    return f'{n} {"day" if n == 1 else "days"}'


def _line(item):
    """One due date as text, e.g. 'NPR 10,000 due on 15 Dec 2026 (7 days late)'."""
    amount = f'NPR {item["amount"]:,.0f}'
    day = item['due_on'].strftime('%-d %b %Y')
    if item['days_late'] > 0:
        return f'{amount} due on {day} ({_days(item["days_late"])} late)'
    if item['days_late'] == 0:
        return f'{amount} due today, {day}'
    return f'{amount} due on {day}'


def _send_email(enrollment, items):
    to = _recipient(enrollment)
    logs = [
        log for log in (
            _reserve(enrollment, item, channel=ReminderLog.Channel.EMAIL, sent_to=to or '')
            for item in items
        ) if log
    ]
    if not logs:
        return
    if not to:
        _finish(logs, ReminderLog.Status.SKIPPED, error='No email address.')
        return

    worst = min((item['step'] for item in items), key=STEP_ORDER.index)
    name = enrollment.student.full_name if enrollment.student else enrollment.lead.full_name
    lines = [_line(item) for item in items]
    context = {
        'name': name,
        'course': enrollment.course.name,
        'title': SUBJECTS[worst],
        'lines': lines,
    }
    try:
        send_mail(
            f'{SUBJECTS[worst]} - Skill Shikshya',
            f'Dear {name},\n\n{enrollment.course.name}:\n' + '\n'.join(lines),
            settings.DEFAULT_FROM_EMAIL,
            [to],
            html_message=render_email('lead/emails/payment_reminder_email.html', context)[0],
            fail_silently=False,
        )
    except Exception as exc:  # noqa: BLE001 — any mail error is logged and retried tomorrow
        logger.warning('Payment reminder to %s failed: %s', to, exc)
        _finish(logs, ReminderLog.Status.FAILED, error=str(exc)[:1000])
        return
    _finish(logs, ReminderLog.Status.SENT)


def _make_call(enrollment, items):
    counselor = enrollment.lead.assigned_counselor
    logs = [
        log for log in (
            _reserve(
                enrollment, item, channel=ReminderLog.Channel.FOLLOW_UP,
                sent_to=counselor.name if counselor else '',
            )
            for item in items
        ) if log
    ]
    if not logs:
        return
    now = timezone.now()
    followup = FollowUp.objects.create(
        lead=enrollment.lead,
        enrollment=enrollment,
        assigned_to=counselor,
        scheduled_at=now,
        reminder_at=now,  # the follow-up reminder job tells the counselor
        purpose=f'Payment late ({enrollment.course.name}): '
                + '; '.join(_line(item) for item in items),
        branch=enrollment.branch,
    )
    _finish(logs, ReminderLog.Status.SENT, followup=followup)


def send_payment_reminders(*, today=None, dry_run=False, config=None):
    """Run today's reminders. Returns what was (or, with dry_run, would be) sent."""
    config = config or ReminderConfig.load()
    today = today or timezone.now().astimezone(NEPAL_TZ).date()
    report = []
    for enrollment in _enrollments():
        items = plan_reminders(enrollment, today=today, config=config)
        if not items:
            continue
        report += [{**item, 'enrollment': enrollment.id} for item in items]
        if dry_run:
            continue
        emails = [item for item in items if item['step'] != Step.CALL]
        calls = [item for item in items if item['step'] == Step.CALL]
        if emails:
            _send_email(enrollment, emails)
        if calls:
            _make_call(enrollment, calls)
    return report
