"""Give old enrollments a seat, once, when seat tracking starts.

Used by migration 0022. It takes the model classes as arguments so the
migration can pass its historical models and tests can pass the real ones.
"""

SEAT_STATUSES = ('confirmed', 'active', 'completed')
REASON = "Seat set from the enrollment's batch when seat tracking started."


def backfill_seats(Enrollment, Placement):
    """Confirmed enrollments with a batch sit in it from their enrollment date.

    Enrollments that already have a seat history are left alone, so running
    it twice changes nothing. Returns how many seats it made.
    """
    made = 0
    enrollments = Enrollment.objects.filter(
        batch__isnull=False, status__in=SEAT_STATUSES, placements__isnull=True,
    )
    for enrollment in enrollments.iterator():
        Placement.objects.create(
            enrollment=enrollment,
            course_batch_id=enrollment.batch_id,
            kind='join',
            started_on=enrollment.enrollment_date,
            reason=REASON,
        )
        Enrollment.objects.filter(pk=enrollment.pk).update(current_batch_id=enrollment.batch_id)
        made += 1
    return made
