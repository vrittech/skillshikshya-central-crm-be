"""Enquiries: one per course a person asks about, each with its own stage.

Stages: new, contacted, interested, then won or lost. Every change saves an
EnquiryStageChange row. Won is set only by confirm_enrollment(), so it always
means the person joined. Lost needs a reason and keeps the stage it died at.
"""
from django.db import transaction
from django.utils import timezone

from apps.lead.models import EnquiryStageChange, LeadCourseInterest
from apps.lead.services import ActionRefused

Stage = LeadCourseInterest.Stage


def _name(enquiry):
    return enquiry.label or 'this enquiry'


def _lock(enquiry):
    return LeadCourseInterest.objects.select_for_update().get(pk=enquiry.pk)


def _change(enquiry, to_stage, *, user, note='', fields=()):
    """Set the stage, save, and add the history row."""
    from_stage = enquiry.stage
    enquiry.stage = to_stage
    enquiry.stage_changed_at = timezone.now()
    enquiry.save(update_fields=['stage', 'stage_changed_at', 'updated_at', *fields])
    EnquiryStageChange.objects.create(
        enquiry=enquiry, from_stage=from_stage, to_stage=to_stage, note=note, changed_by=user,
    )
    return enquiry


def open_enquiry_for(lead, course):
    """The lead's open enquiry for the course, if any."""
    return lead.course_interests.filter(
        course=course, stage__in=LeadCourseInterest.OPEN_STAGES,
    ).first()


@transaction.atomic
def add_enquiry(lead, *, course=None, course_name=None, user=None, source=None, counselor=None,
                is_primary=False, interest_level=None, branch=None, about='', description=''):
    """A new enquiry, at New. One open enquiry per course per lead.

    The source and counselor default to the lead's; the branch to the branch
    of the user taking the enquiry, else the lead's.
    """
    if course is not None and open_enquiry_for(lead, course):
        raise ActionRefused(f'{lead.full_name} already has an open enquiry for {course.name}.')
    return LeadCourseInterest.objects.create(
        lead=lead,
        course=course,
        course_name=course_name if course is None else None,
        about=(about or '').strip(),
        description=(description or '').strip(),
        is_primary=is_primary,
        interest_level=interest_level or LeadCourseInterest.InterestLevel.MEDIUM,
        source=source or lead.source,
        counselor=counselor or lead.assigned_counselor,
        branch=branch or getattr(user, 'branch', None) or lead.branch,
        created_by=user,
    )


@transaction.atomic
def move_stage(enquiry, stage, *, user, note=''):
    """Move an enquiry to an open stage. Bringing back a lost one needs a note."""
    enquiry = _lock(enquiry)
    if stage not in LeadCourseInterest.OPEN_STAGES:
        raise ActionRefused('Pick New, Contacted or Interested. Won is set by confirming an enrollment.')
    if enquiry.stage == Stage.WON:
        raise ActionRefused(f'The enquiry for {_name(enquiry)} is won: the person joined.')
    if enquiry.stage == stage:
        raise ActionRefused(f'The enquiry is already {enquiry.get_stage_display().lower()}.')
    fields = []
    if enquiry.stage == Stage.LOST:
        if not note.strip():
            raise ActionRefused('Say why the lost enquiry is opened again.')
        enquiry.lost_reason = None
        enquiry.lost_note = ''
        enquiry.lost_at_stage = None
        fields = ['lost_reason', 'lost_note', 'lost_at_stage']
    return _change(enquiry, stage, user=user, note=note, fields=fields)


@transaction.atomic
def mark_lost(enquiry, *, reason, user, note=''):
    enquiry = _lock(enquiry)
    if enquiry.stage not in LeadCourseInterest.OPEN_STAGES:
        raise ActionRefused(
            f'Only an open enquiry can be lost. This one is {enquiry.get_stage_display().lower()}.'
        )
    if reason not in LeadCourseInterest.LostReason.values:
        raise ActionRefused('Pick a reason from the list.')
    note = (note or '').strip()
    if reason == LeadCourseInterest.LostReason.OTHER and not note:
        raise ActionRefused('Write the reason in the note.')
    enquiry.lost_at_stage = enquiry.stage
    enquiry.lost_reason = reason
    enquiry.lost_note = note
    history_note = enquiry.get_lost_reason_display() + (f': {note}' if note else '')
    return _change(
        enquiry, Stage.LOST, user=user, note=history_note,
        fields=['lost_at_stage', 'lost_reason', 'lost_note'],
    )


def win_for_enrollment(enrollment, *, user):
    """Called inside confirm_enrollment: the enquiry for the course is won.

    The open enquiry is used first, then a lost one (the person came back).
    A person who joins without an enquiry (a walk-in) gets one, so every
    joiner is counted in the enquiry report.
    """
    lead = enrollment.lead
    enquiries = lead.course_interests.select_for_update().filter(course=enrollment.course)
    enquiry = (
        enquiries.filter(stage__in=LeadCourseInterest.OPEN_STAGES)
        .order_by('-is_primary', '-created_at').first()
        or enquiries.filter(stage=Stage.LOST).order_by('-stage_changed_at').first()
    )
    if enquiry is None:
        enquiry = LeadCourseInterest.objects.create(
            lead=lead,
            course=enrollment.course,
            is_primary=not lead.course_interests.exists(),
            source=lead.source,
            counselor=lead.assigned_counselor,
            branch=enrollment.branch or lead.branch,
            created_by=user,
        )
    enquiry.enrollment = enrollment
    return _change(enquiry, Stage.WON, user=user, note='Enrollment confirmed.', fields=['enrollment'])


def ensure_primary_enquiry(lead, *, user):
    """The lead's main course always has an enquiry, marked primary."""
    course = lead.primary_course
    if course is None:
        return None
    enquiry = (
        lead.course_interests.filter(course=course)
        .exclude(stage=Stage.LOST).order_by('-created_at').first()
    )
    if enquiry is None:
        enquiry = add_enquiry(lead, course=course, user=user, is_primary=True)
    lead.course_interests.exclude(pk=enquiry.pk).filter(is_primary=True).update(is_primary=False)
    if not enquiry.is_primary:
        enquiry.is_primary = True
        enquiry.save(update_fields=['is_primary', 'updated_at'])
    return enquiry
