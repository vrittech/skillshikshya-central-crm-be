"""Microsoft Teams licences: who has one, and freeing them with a reason.

A licence is given to a student sitting in a batch. It is freed by hand with a
reason, or on its own when the student cancels or their batch closes. When the
student pauses, the licence is paused: kept for them and still counted, and in
use again when they resume. Each change also writes an EnrollmentHistory row.
"""
from django.db import transaction
from django.utils import timezone

from apps.lead.models import Enrollment, EnrollmentHistory, TeamsConfig, TeamsLicence
from apps.lead.services import ActionRefused, _lock


def _name(enrollment):
    student = enrollment.student
    return student.full_name if student else enrollment.lead.full_name


def open_licences():
    return TeamsLicence.objects.filter(released_at__isnull=True)


def summary():
    """{'total', 'in_use', 'paused', 'free'}. Total and free are None when no total is set.

    A paused licence is still the student's, so it is not free.
    """
    total = TeamsConfig.load().total_licences
    taken = open_licences().count()
    paused = open_licences().filter(paused_at__isnull=False).count()
    return {
        'total': total,
        'in_use': taken - paused,
        'paused': paused,
        'free': None if total is None else max(total - taken, 0),
    }


@transaction.atomic
def give_licence(enrollment, *, account, user):
    # Locking the settings row makes two gives wait for each other, so the
    # count of licences in use can't go past the total.
    config, _ = TeamsConfig.objects.select_for_update().get_or_create(pk=1)
    enrollment = _lock(enrollment)
    if enrollment.status not in Enrollment.SEAT_STATUSES or not enrollment.current_batch_id:
        raise ActionRefused('Only a student sitting in a batch can get a Teams licence.')
    if open_licences().filter(enrollment=enrollment).exists():
        raise ActionRefused('This student already has a Teams licence.')
    account = account.strip().lower()
    holder = open_licences().filter(account__iexact=account).select_related(
        'enrollment__lead', 'enrollment__student',
    ).first()
    if holder is not None:
        raise ActionRefused(
            f'{account} is in use by {_name(holder.enrollment)}. Free it first.'
        )
    in_use = open_licences().count()
    if config.total_licences is not None and in_use >= config.total_licences:
        raise ActionRefused(
            f'All {config.total_licences} Teams licences are in use. Free one first.'
        )
    licence = TeamsLicence.objects.create(
        enrollment=enrollment, account=account, branch=enrollment.branch, assigned_by=user,
    )
    EnrollmentHistory.record(
        enrollment=enrollment,
        action=EnrollmentHistory.Action.GIVE_LICENCE,
        reason='Teams licence given.',
        performed_by=user,
        before={'teams_account': None},
        after={'teams_account': account},
    )
    return licence


def _free(licence, *, reason, user):
    licence.released_at = timezone.now()
    licence.released_by = user
    licence.release_reason = reason
    licence.save(update_fields=['released_at', 'released_by', 'release_reason'])
    EnrollmentHistory.record(
        enrollment=licence.enrollment,
        action=EnrollmentHistory.Action.FREE_LICENCE,
        reason=reason,
        performed_by=user,
        before={'teams_account': licence.account},
        after={'teams_account': None},
    )
    return licence


@transaction.atomic
def free_licence(licence, *, reason, user):
    licence = TeamsLicence.objects.select_for_update().get(pk=licence.pk)
    if not licence.is_open:
        raise ActionRefused('This licence is already free.')
    return _free(licence, reason=reason, user=user)


def free_for_enrollment(enrollment, *, reason, user):
    """Free the enrollment's licence, if it has one. Called inside pause and cancel."""
    for licence in open_licences().select_for_update().filter(enrollment=enrollment):
        _free(licence, reason=reason, user=user)


def free_for_batch(batch, *, reason, user):
    """Free the licences of students sitting in the batch. Called inside close batch."""
    for licence in open_licences().select_for_update().filter(enrollment__current_batch=batch):
        _free(licence, reason=reason, user=user)


def pause_for_enrollment(enrollment, *, reason, user):
    """Keep the enrollment's licence for the student while paused. Called inside pause."""
    for licence in open_licences().select_for_update().filter(
        enrollment=enrollment, paused_at__isnull=True,
    ):
        licence.paused_at = timezone.now()
        licence.save(update_fields=['paused_at'])
        EnrollmentHistory.record(
            enrollment=enrollment,
            action=EnrollmentHistory.Action.PAUSE_LICENCE,
            reason=reason,
            performed_by=user,
            before={'teams_account': licence.account, 'teams_licence': 'in use'},
            after={'teams_account': licence.account, 'teams_licence': 'paused'},
        )


def resume_for_enrollment(enrollment, *, reason, user):
    """Put the paused licence back in use. Called inside resume."""
    for licence in open_licences().select_for_update().filter(
        enrollment=enrollment, paused_at__isnull=False,
    ):
        licence.paused_at = None
        licence.save(update_fields=['paused_at'])
        EnrollmentHistory.record(
            enrollment=enrollment,
            action=EnrollmentHistory.Action.RESUME_LICENCE,
            reason=reason,
            performed_by=user,
            before={'teams_account': licence.account, 'teams_licence': 'paused'},
            after={'teams_account': licence.account, 'teams_licence': 'in use'},
        )

