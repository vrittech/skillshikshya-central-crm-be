"""Closing a batch properly: the close checklist, completing its students, and its register.

Closing marks every student sitting in the batch (confirmed or active)
completed, each with a history row. Paused students and students who moved
to another batch are not in it, so closing leaves them alone. Reopening puts
the students that close completed back as they were.

Before a batch closes, the checklist looks at:

- students: who closing will mark completed, and anyone still pending for
  the batch (pending is open: cancel them or move them first).
- attendance: every student in the batch has a mark on every register day
  since they sat in it. A register day is a day anyone in the batch was marked.
- teams: no student in the batch still holds a Teams licence.
- owing: who still owes money on the batch. For information only; it never
  stops the close.

A batch with open items closes only with close_anyway, and what was open is
saved on the close row (BatchClosing.checklist).
"""
from datetime import datetime

from django.db import transaction
from django.db.models import Count, Q

from apps.course.models import CourseBatch
from apps.course.money import batch_owing
from apps.lead.services import ActionRefused

# Students still studying. Closing the batch marks them completed.
STUDYING = ('confirmed', 'active')


def nepal_today():
    from apps.lead.filters import NEPAL

    return datetime.now(NEPAL).date()


def _name(e):
    return e.student.full_name if e.student_id else e.lead.full_name


def _person(e):
    return {
        'enrollment': e.id,
        'lead': e.lead_id,
        'name': _name(e),
        'student_id': e.student.student_id if e.student_id else None,
    }


def seated(batch):
    """Enrollments sitting in the batch now, with the day each sat in it (seat_on)."""
    from apps.lead.models import Enrollment
    from apps.lead.services import with_seat

    return with_seat(
        Enrollment.objects.filter(current_batch=batch, status__in=Enrollment.SEAT_STATUSES)
    ).select_related('lead', 'student').order_by('lead__full_name', 'id')


def window(batch, today=None):
    """The days attendance counts for: the batch's start to its end, never past today."""
    today = today or nepal_today()
    end = min(batch.end_date, today) if batch.end_date else today
    return batch.start_date, end


def _in_window(qs, batch):
    start, end = window(batch)
    if start:
        qs = qs.filter(date__gte=start)
    return qs.filter(date__lte=end)


# ---------------------------------------------------------------- checklist


def _students(batch, rows):
    """(who closing marks completed, who is still pending for the batch)."""
    from apps.lead.models import Enrollment

    labels = dict(Enrollment.Status.choices)

    def person(e):
        return {**_person(e), 'status': e.status, 'status_display': labels[e.status]}

    pending = Enrollment.objects.filter(
        batch=batch, status=Enrollment.Status.PENDING,
    ).select_related('lead', 'student').order_by('lead__full_name', 'id')
    return [person(e) for e in rows if e.status in STUDYING], [person(e) for e in pending]


def _attendance(batch, rows):
    from apps.student.models import Attendance

    student_ids = [e.student_id for e in rows if e.student_id]
    marks = _in_window(
        Attendance.objects.filter(course_id=batch.course_id, student_id__in=student_ids), batch,
    ).values_list('student_id', 'date')
    marked = {}
    for student_id, day in marks:
        marked.setdefault(student_id, set()).add(day)
    days = sorted(set().union(*marked.values())) if marked else []

    missing = []
    for e in rows:
        expected = [d for d in days if e.seat_on is None or d >= e.seat_on]
        gaps = [d for d in expected if d not in marked.get(e.student_id, set())]
        if gaps:
            missing.append({
                **_person(e),
                'missing': len(gaps),
                'dates': [d.isoformat() for d in gaps[:5]],
            })
    none_taken = bool(rows) and not days
    return {
        'days': len(days),
        'last_day': days[-1].isoformat() if days else None,
        'none_taken': none_taken,
        'missing': missing,
        'ready': not none_taken and not missing,
    }


def _teams(rows):
    from apps.lead import teams

    licences = teams.open_licences().filter(
        enrollment__in=[e.id for e in rows],
    ).select_related('enrollment__lead', 'enrollment__student', 'enrollment__current_batch')
    out = []
    for licence in licences.order_by('enrollment__lead__full_name', 'id'):
        state, why = teams.licence_state(licence)
        out.append({
            **_person(licence.enrollment),
            'licence': licence.id,
            'account': licence.account,
            'state': state,
            'state_display': teams.STATE_LABELS[state],
            'reason': why,
        })
    return out


def _owing(batch):
    rows = batch_owing(batch.pk)
    return {
        'count': len(rows),
        'total': str(sum((owed for _, owed in rows), 0)),
        'people': [{**_person(e), 'owed': str(owed)} for e, owed in rows],
    }


def close_check(batch):
    """What is still open before the batch can close. JSON-ready."""
    rows = list(seated(batch))
    completing, pending = _students(batch, rows)
    attendance = _attendance(batch, rows)
    licences = _teams(rows)
    return {
        'batch': batch.pk,
        'status': batch.status,
        'ready': not pending and attendance['ready'] and not licences,
        'completing': completing,
        'pending': pending,
        'attendance': attendance,
        'teams': licences,
        'owing': _owing(batch),
    }


def check_record(check, *, close_anyway):
    """The part of the checklist kept on the close row: who was still open."""
    def names(rows):
        return [r['name'] for r in rows]

    return {
        'ready': check['ready'],
        'closed_anyway': close_anyway and not check['ready'],
        'pending': names(check['pending']),
        'attendance_none_taken': check['attendance']['none_taken'],
        'attendance_missing': names(check['attendance']['missing']),
        'teams': names(check['teams']),
        'owing_count': check['owing']['count'],
        'owing_total': check['owing']['total'],
    }


class CloseBlocked(ActionRefused):
    default_detail = 'This batch still has open items. Tick close anyway to close it.'

    def __init__(self, check):
        super().__init__()
        self.check = check


# ---------------------------------------------------------------- students


def complete_seated(batch, *, reason, user):
    """Mark students sitting in the batch completed, each with a history row.

    Called by close_batch inside its transaction. Returns who was completed
    and their status before, to keep on the close row.
    """
    from apps.lead.models import Enrollment, EnrollmentHistory

    studying = Enrollment.objects.select_for_update().filter(
        current_batch=batch, status__in=STUDYING,
    ).order_by('id')
    done = []
    for e in studying:
        before = e.status
        e.status = Enrollment.Status.COMPLETED
        e.updated_by = user
        e.save(update_fields=['status', 'updated_by', 'updated_at'])
        EnrollmentHistory.record(
            enrollment=e,
            action=EnrollmentHistory.Action.COMPLETE,
            reason=reason,
            performed_by=user,
            before={'status': before},
            after={'status': Enrollment.Status.COMPLETED},
        )
        done.append({'enrollment': e.id, 'before': before})
    return done


def undo_completed(batch, completed, *, reason, user):
    """Put the students a close completed back to their status before it.

    Only those still completed and still sitting in the batch. Returns how many.
    """
    from apps.lead.models import Enrollment, EnrollmentHistory

    before = {row['enrollment']: row['before'] for row in completed}
    back = Enrollment.objects.select_for_update().filter(
        pk__in=list(before), current_batch=batch, status=Enrollment.Status.COMPLETED,
    ).order_by('id')
    count = 0
    for e in back:
        e.status = before[e.id]
        e.updated_by = user
        e.save(update_fields=['status', 'updated_by', 'updated_at'])
        EnrollmentHistory.record(
            enrollment=e,
            action=EnrollmentHistory.Action.UNDO_COMPLETE,
            reason=reason,
            performed_by=user,
            before={'status': Enrollment.Status.COMPLETED},
            after={'status': e.status},
        )
        count += 1
    return count


# ---------------------------------------------------------------- register


def _lock(batch):
    return CourseBatch.objects.select_for_update().get(pk=batch.pk)


def register(batch, day):
    """The batch's students with their mark on day and their totals in the batch."""
    from apps.student.models import Attendance

    rows = list(seated(batch))
    student_ids = [e.student_id for e in rows if e.student_id]
    in_course = Attendance.objects.filter(course_id=batch.course_id, student_id__in=student_ids)
    on_day = {a.student_id: a for a in in_course.filter(date=day)}
    totals = {
        t['student_id']: t
        for t in _in_window(in_course, batch).values('student_id').annotate(
            present=Count('id', filter=Q(status=Attendance.Status.PRESENT)),
            absent=Count('id', filter=Q(status=Attendance.Status.ABSENT)),
        )
    }
    days = _in_window(in_course, batch).values_list('date', flat=True).distinct().order_by('-date')

    def row(e):
        mark = on_day.get(e.student_id)
        t = totals.get(e.student_id, {})
        present, absent = t.get('present', 0), t.get('absent', 0)
        return {
            **_person(e),
            'student': e.student_id,
            'since': e.seat_on.isoformat() if e.seat_on else None,
            'status': mark.status if mark else None,
            'present': present,
            'absent': absent,
            'total': present + absent,
        }

    return {
        'batch': batch.pk,
        'date': day.isoformat(),
        'today': nepal_today().isoformat(),
        'closed': batch.status == CourseBatch.Status.CLOSED,
        'days': [d.isoformat() for d in days],
        'students': [row(e) for e in rows],
    }


@transaction.atomic
def mark_register(batch, *, day, marks, user, can_change):
    """Save the day's marks for students in the batch. Returns (created, changed).

    marks: [{'student': id, 'status': 'present' | 'absent'}]. A mark that
    already exists is changed only when can_change.
    """
    from apps.student.models import Attendance

    batch = _lock(batch)
    if batch.status == CourseBatch.Status.CLOSED:
        raise ActionRefused('This batch is closed. Its attendance can no longer change.')
    if day > nepal_today():
        raise ActionRefused('Attendance can’t be marked for a future day.')
    if batch.start_date and day < batch.start_date:
        raise ActionRefused('This day is before the batch starts.')
    in_batch = {e.student_id for e in seated(batch) if e.student_id}
    wanted = {m['student']: m['status'] for m in marks}
    if not wanted:
        raise ActionRefused('Mark at least one student.')
    if set(wanted) - in_batch:
        raise ActionRefused('Some of these students are not in this batch.')

    existing = {
        a.student_id: a
        for a in Attendance.objects.select_for_update().filter(
            course_id=batch.course_id, date=day, student_id__in=list(wanted),
        )
    }
    to_change = [a for sid, a in existing.items() if a.status != wanted[sid]]
    if to_change and not can_change:
        raise ActionRefused('You can mark new attendance but not change marks already saved.')
    for a in to_change:
        a.status = wanted[a.student_id]
        a.marked_by = user
        a.save(update_fields=['status', 'marked_by'])
    new = [
        Attendance(student_id=sid, course_id=batch.course_id, date=day, status=status, marked_by=user)
        for sid, status in wanted.items() if sid not in existing
    ]
    Attendance.objects.bulk_create(new)
    return len(new), len(to_change)
