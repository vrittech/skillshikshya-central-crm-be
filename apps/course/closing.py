"""Closing a batch properly: the close checklist, completing its students, and its register.

Closing marks every student sitting in the batch (confirmed or active)
completed, each with a history row. Paused students and students who moved
to another batch are not in it, so closing leaves them alone. Reopening puts
the students that close completed back as they were.

Before a batch closes, the checklist looks at:

- students: who closing will mark completed, and anyone still pending for
  the batch (pending is open: cancel them or move them first).
- attendance: every student in the batch has a mark on every register day
  since they sat in it. A register day is a day anyone in the batch was
  marked; a mark counts for the batch the student's seat history puts them
  in that day.
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


def _sat_in(history, day):
    """(batch id, seated) for a day, by an enrollment's seat history (oldest row first).

    The newest row that started on or before the day says where the student
    sat. Before their first row they sat nowhere yet (seated is False), but a
    mark of those days belongs to the batch they first joined: its register
    lets earlier days be filled in.
    """
    rows = [p for p in history if p.started_on <= day]
    if not rows:
        return history[0].course_batch_id, False
    return rows[-1].course_batch_id, True


def _attendance(batch, rows):
    """A student's marks count here only for the days their seat history puts
    them in this batch, so a student who moved in brings no class days from
    their old batch."""
    from apps.lead.models import Placement
    from apps.student.models import Attendance

    history = {}
    for p in Placement.objects.filter(enrollment__in=[e.id for e in rows]).order_by('created_at', 'id'):
        history.setdefault(p.enrollment_id, []).append(p)

    def counts(e, day):
        # Without seat history every day counts, and is expected.
        return e.id not in history or _sat_in(history[e.id], day)[0] == batch.pk

    def expected(e, day):
        return e.id not in history or _sat_in(history[e.id], day) == (batch.pk, True)

    student_ids = [e.student_id for e in rows if e.student_id]
    marks = _in_window(
        Attendance.objects.filter(course_id=batch.course_id, student_id__in=student_ids), batch,
    ).values_list('student_id', 'date')
    by_student = {}
    for student_id, day in marks:
        by_student.setdefault(student_id, set()).add(day)
    marked = {
        e.id: {d for d in by_student.get(e.student_id, ()) if counts(e, d)}
        for e in rows if e.student_id
    }
    days = sorted(set().union(*marked.values())) if marked else []

    missing = []
    for e in rows:
        gaps = [d for d in days if expected(e, d) and d not in marked.get(e.id, set())]
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


def day_refusal(batch, day):
    """Why the batch's register can't take marks on day, or None when it can."""
    if batch.status == CourseBatch.Status.CLOSED:
        return 'This batch is closed. Its attendance can no longer change.'
    if day > nepal_today():
        return 'Attendance can’t be marked for a future day.'
    if batch.start_date and day < batch.start_date:
        return 'This day is before the batch starts.'
    if batch.end_date and day > batch.end_date:
        return 'This day is after the batch ends.'
    return None


@transaction.atomic
def mark_register(batch, *, day, marks, user, can_change):
    """Save the day's marks for students in the batch. Returns (created, changed).

    marks: [{'student': id, 'status': 'present' | 'absent'}]. A mark that
    already exists is changed only when can_change.
    """
    from apps.student.models import Attendance

    batch = _lock(batch)
    refusal = day_refusal(batch, day)
    if refusal:
        raise ActionRefused(refusal)
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


def check_one_mark(student_id, course_id, day):
    """A mark saved outside the register keeps the register's rules.

    The student sits in a batch of the course now, and that batch takes marks
    on the day. Raises ActionRefused otherwise. Call it inside a transaction:
    it locks the batch rows, as the register does.
    """
    from apps.lead.models import Enrollment

    if course_id is None:
        raise ActionRefused('Pick a course.')
    batch_ids = Enrollment.objects.filter(
        student_id=student_id, course_id=course_id, current_batch__isnull=False,
        status__in=Enrollment.SEAT_STATUSES,
    ).values_list('current_batch', flat=True)
    batches = CourseBatch.objects.select_for_update().filter(pk__in=list(batch_ids)).order_by('pk')
    refusals = [day_refusal(b, day) for b in batches]
    if not refusals:
        raise ActionRefused('This student is not in a batch of this course.')
    if all(refusals):
        raise ActionRefused(refusals[0])
