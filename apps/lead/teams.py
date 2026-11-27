"""Microsoft Teams licences: the accounts we own, who has each one, and which to take back.

A TeamsAccount is one Teams sign-in the institute owns; the active ones are
the licence total. Giving one to a student adds a TeamsLicence row. Taking it
back needs a reason, keeps the row, and makes the account ready to give again.

The CRM can't remove anyone from Microsoft 365, so it never takes a licence
back on its own. Each account shows a state worked out from the student's
enrollment, so staff know what to do in Microsoft 365:

- ready: no one has it, it can be given.
- in_use: the student is studying.
- safe_to_remove: the student completed the course.
- action_required: the student dropped out, cancelled, paused, moved away,
  or their batch closed. Someone decides whether to take it back.
- retired: removed from Microsoft 365. Kept for the record, not counted.

While a student is paused their licence is marked paused (paused_at), and
resuming clears it. Each give, take back, pause and resume writes an
EnrollmentHistory row.
"""
from django.db import transaction
from django.db.models import Prefetch, Q
from django.utils import timezone

from apps.course.models import CourseBatch
from apps.lead.models import Enrollment, EnrollmentHistory, TeamsAccount, TeamsLicence
from apps.lead.services import ActionRefused, _lock

READY, IN_USE, SAFE, ACTION, RETIRED = 'ready', 'in_use', 'safe_to_remove', 'action_required', 'retired'
STATE_LABELS = {
    READY: 'Ready to give',
    IN_USE: 'In use',
    SAFE: 'Safe to remove',
    ACTION: 'Action required',
    RETIRED: 'Retired',
}

Status = Enrollment.Status
ENROLLMENT_REASONS = {
    Status.COMPLETED: (SAFE, 'Course completed'),
    Status.DROPPED_OUT: (ACTION, 'Dropped out'),
    Status.CANCELLED: (ACTION, 'Enrollment cancelled'),
    Status.TRANSFERRED: (ACTION, 'Transferred'),
}


def _name(enrollment):
    student = enrollment.student
    return student.full_name if student else enrollment.lead.full_name


def open_licences():
    return TeamsLicence.objects.filter(released_at__isnull=True)


def licence_state(licence):
    """(state, reason) for a licence a student holds now."""
    enrollment = licence.enrollment
    if enrollment.status in ENROLLMENT_REASONS:
        return ENROLLMENT_REASONS[enrollment.status]
    if licence.paused_at:
        return ACTION, 'Paused'
    batch = enrollment.current_batch
    if batch is None:
        return ACTION, 'Not in a batch'
    if batch.status == CourseBatch.Status.CLOSED:
        return ACTION, 'Batch closed'
    return IN_USE, ''


def visible_accounts(branch_id=None):
    """Accounts with the open licence prefetched. A branch sees its own and the shared ones."""
    accounts = TeamsAccount.objects.select_related('branch', 'retired_by').prefetch_related(
        Prefetch(
            'licences',
            queryset=open_licences().select_related(
                'enrollment__lead', 'enrollment__student', 'enrollment__course',
                'enrollment__current_batch', 'enrollment__branch', 'assigned_by',
            ),
            to_attr='open_licences',
        ),
    )
    if branch_id is not None:
        accounts = accounts.filter(Q(branch_id=branch_id) | Q(branch__isnull=True))
    return accounts


def account_row(account, *, branch_id=None):
    """One account as the Teams page shows it."""
    licence = account.open_licences[0] if account.open_licences else None
    if not account.is_active:
        state, reason = RETIRED, account.retire_reason
    elif licence is None:
        state, reason = READY, ''
    else:
        state, reason = licence_state(licence)
    row = {
        'id': account.id,
        'email': account.email,
        'branch': account.branch_id,
        'branch_name': account.branch.name if account.branch_id else None,
        'is_active': account.is_active,
        'state': state,
        'state_label': STATE_LABELS[state],
        'reason': reason,
        'retired_at': account.retired_at,
        'licence': None,
    }
    if licence is None:
        return row
    enrollment = licence.enrollment
    if branch_id is not None and enrollment.branch_id != branch_id:
        # A shared account in use at another branch: no student details.
        row['licence'] = {'id': licence.id, 'other_branch': True}
        return row
    row['licence'] = {
        'id': licence.id,
        'other_branch': False,
        'enrollment': enrollment.id,
        'enrollment_status': enrollment.status,
        'lead': enrollment.lead_id,
        'student_id': enrollment.student.student_id if enrollment.student_id else None,
        'name': _name(enrollment),
        'email': (enrollment.student.email if enrollment.student_id else None) or enrollment.lead.email,
        'course_name': enrollment.course.name,
        'batch_name': enrollment.current_batch.name if enrollment.current_batch_id else None,
        'branch_name': enrollment.branch.name if enrollment.branch_id else None,
        'assigned_at': licence.assigned_at,
        'assigned_by_name': licence.assigned_by.name if licence.assigned_by_id else None,
        'paused_at': licence.paused_at,
        'details_sent_at': licence.details_sent_at,
        'details_sent_to': licence.details_sent_to,
    }
    return row


def account_rows(branch_id=None):
    return [account_row(a, branch_id=branch_id) for a in visible_accounts(branch_id)]


def summary(branch_id=None):
    """How many accounts are in each state. Total = active accounts."""
    counts = {state: 0 for state in STATE_LABELS}
    for row in account_rows(branch_id):
        counts[row['state']] += 1
    return {
        'total': sum(n for state, n in counts.items() if state != RETIRED),
        'ready': counts[READY],
        'in_use': counts[IN_USE],
        'safe_to_remove': counts[SAFE],
        'action_required': counts[ACTION],
        'retired': counts[RETIRED],
    }


@transaction.atomic
def add_accounts(emails, *, branch, user):
    """Add Teams accounts to the pool. Returns (added emails, emails already there)."""
    added, existing = [], []
    for email in dict.fromkeys(e.strip().lower() for e in emails if e and e.strip()):
        _, created = TeamsAccount.objects.get_or_create(
            email=email, defaults={'branch': branch, 'created_by': user},
        )
        (added if created else existing).append(email)
    return added, existing


def _lock_account(account):
    return TeamsAccount.objects.select_for_update().get(pk=account.pk)


@transaction.atomic
def give_licence(enrollment, *, teams_account, user):
    """Give a ready account to a student sitting in a batch."""
    account = _lock_account(teams_account)
    enrollment = _lock(enrollment)
    if enrollment.status not in Enrollment.SEAT_STATUSES or not enrollment.current_batch_id:
        raise ActionRefused('Only a student sitting in a batch can get a Teams licence.')
    if open_licences().filter(enrollment=enrollment).exists():
        raise ActionRefused('This student already has a Teams licence.')
    if not account.is_active:
        raise ActionRefused(f'{account.email} is retired. Pick another account.')
    holder = open_licences().filter(teams_account=account).select_related(
        'enrollment__lead', 'enrollment__student',
    ).first()
    if holder is not None:
        raise ActionRefused(f'{account.email} is in use by {_name(holder.enrollment)}. Take it back first.')
    if account.branch_id and account.branch_id != enrollment.branch_id:
        raise ActionRefused(f'{account.email} belongs to {account.branch.name}. Pick an account of this branch.')
    licence = TeamsLicence.objects.create(
        enrollment=enrollment, teams_account=account, account=account.email,
        branch=enrollment.branch, assigned_by=user,
    )
    EnrollmentHistory.record(
        enrollment=enrollment,
        action=EnrollmentHistory.Action.GIVE_LICENCE,
        reason='Teams licence given.',
        performed_by=user,
        before={'teams_account': None},
        after={'teams_account': account.email},
    )
    return licence


def send_details(licence, *, password, user):
    """Email the student their Teams sign-in. The password is sent, never kept."""
    from apps.global_config.models import EmailLog
    from apps.lead.emails import teams_details

    if not licence.is_open:
        raise ActionRefused('This licence was taken back.')
    log = teams_details(licence, password, user=user)
    if log.status == EmailLog.Status.SENT:
        licence.details_sent_at = log.sent_at
        licence.details_sent_to = log.to_email
        licence.save(update_fields=['details_sent_at', 'details_sent_to'])
    return log


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
    """Take the licence back. The account is ready to give again."""
    licence = TeamsLicence.objects.select_for_update().get(pk=licence.pk)
    if not licence.is_open:
        raise ActionRefused('This licence was already taken back.')
    return _free(licence, reason=reason, user=user)


@transaction.atomic
def retire_account(account, *, reason, user):
    """Stop counting an account (it was removed from Microsoft 365)."""
    account = _lock_account(account)
    if not account.is_active:
        raise ActionRefused('This account is already retired.')
    if open_licences().filter(teams_account=account).exists():
        raise ActionRefused('Take this account back from the student first.')
    account.is_active = False
    account.retired_at = timezone.now()
    account.retired_by = user
    account.retire_reason = reason
    account.save(update_fields=list(TeamsAccount.RETIRE_FIELDS))
    return account


@transaction.atomic
def restore_account(account):
    account = _lock_account(account)
    if account.is_active:
        raise ActionRefused('This account is in use already.')
    account.is_active = True
    account.retired_at = None
    account.retired_by = None
    account.retire_reason = ''
    account.save(update_fields=list(TeamsAccount.RETIRE_FIELDS))
    return account


def pause_for_enrollment(enrollment, *, reason, user):
    """Mark the enrollment's licence paused while the student is paused. Called inside pause."""
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
