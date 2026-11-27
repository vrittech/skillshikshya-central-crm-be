"""Everything about a person in one list, newest first.

Read-only: it is built from the records the other steps already keep
(enquiries and their stage changes, calls, notes, follow-ups, enrollment
history, payments, invoices, reminders, documents and voice notes).
Nothing is stored for it. Emails the system sends are left out: nobody
did anything there.

Each item: id, kind, at, title, detail, by, course, enrollment, enquiry,
voice_note, amount, duration_seconds, before, after. `kind` is one of KINDS,
so the screen can show one kind at a time.

A person may enquire at several branches. A branch user sees only their
branch's records here, as in every list: no other branch's students or money.
"""
from apps.invoices.models import Invoice
from apps.lead.models import (
    EnquiryStageChange, EnrollmentHistory, FollowUp, LeadActivity, LeadCall, LeadChange,
    LeadDocument, Payment, ReminderLog, VoiceNote,
)

KINDS = [
    'lead', 'enquiry', 'call', 'note', 'follow_up', 'voice_note', 'enrollment', 'batch',
    'money', 'teams', 'reminder', 'document',
]

Action = EnrollmentHistory.Action
HISTORY_KIND = {
    Action.CONFIRM: 'enrollment',
    Action.CANCEL: 'enrollment',
    Action.REINSTATE: 'enrollment',
    Action.JOIN_BATCH: 'batch',
    Action.SWITCH_BATCH: 'batch',
    Action.PAUSE: 'batch',
    Action.RESUME: 'batch',
    Action.GIVE_LICENCE: 'teams',
    Action.FREE_LICENCE: 'teams',
    Action.PAUSE_LICENCE: 'teams',
    Action.RESUME_LICENCE: 'teams',
    Action.COMPLETE: 'enrollment',
    Action.UNDO_COMPLETE: 'enrollment',
    Action.CHANGE_STATUS: 'enrollment',
    Action.MOVE_BRANCH: 'enrollment',
}  # everything else is money: fee changes, charges, discounts, refunds, voids, invoices, due dates

# History rows about money: shown only to users who can see payments.
MONEY_ACTIONS = [action for action in Action.values if action not in HISTORY_KIND]

# The right each kind needs. Fees, paid and left on an enrollment come with
# the enrollment; single payments, refunds and invoices need view_payment.
KIND_RIGHTS = {
    'lead': 'lead.view_lead',
    'enquiry': 'lead.view_leadcourseinterest',
    'call': 'lead.view_leadcall',
    'note': 'lead.view_leadactivity',
    'follow_up': 'lead.view_followup',
    'voice_note': 'lead.view_voicenote',
    'enrollment': 'lead.view_enrollment',
    'batch': 'lead.view_enrollment',
    'teams': 'lead.view_enrollment',
    'reminder': 'lead.view_enrollment',
    'money': 'lead.view_payment',
    'document': 'lead.view_leaddocument',
}
assert set(KIND_RIGHTS) == set(KINDS)


def visible_kinds(user):
    """The kinds of history this user may see."""
    from apps.global_config.permissions import can

    return {kind for kind, right in KIND_RIGHTS.items() if can(user, right)}

INVOICE_TITLE = {Invoice.Type.ENROLLMENT: 'Fee invoice', Invoice.Type.PAYMENT: 'Receipt'}


def _name(user):
    return user.name if user else None


def _course(course, fallback=None):
    return course.name if course else fallback


def _item(kind, key, at, title, **extra):
    return {
        'id': f'{kind}-{key}',
        'kind': kind,
        'at': at,
        'title': title,
        'detail': extra.pop('detail', '') or '',
        'by': extra.pop('by', None),
        'course': extra.pop('course', None),
        'enrollment': extra.pop('enrollment', None),
        'enquiry': extra.pop('enquiry', None),
        'voice_note': extra.pop('voice_note', None),
        'amount': extra.pop('amount', None),
        'duration_seconds': extra.pop('duration_seconds', None),
        'before': extra.pop('before', None),
        'after': extra.pop('after', None),
    }


def _join(*parts):
    return ' · '.join(str(p) for p in parts if p)


def lead_timeline(lead, *, branch_id=None):
    """branch_id: show only that branch's records (a branch user's view). None = every branch."""

    def mine(qs, field='branch_id'):
        return qs if branch_id is None else qs.filter(**{field: branch_id})

    items = [
        _item(
            'lead', lead.pk, lead.created_at, 'Lead added',
            detail=_join(
                lead.reference_number, lead.source.name if lead.source else None,
                f'Imported from {lead.custom_data["import_file"]}'
                if (lead.custom_data or {}).get('import_file') else None,
            ),
            by=_name(lead.created_by),
        ),
    ]

    for c in LeadChange.objects.filter(lead=lead).select_related('changed_by'):
        label = c.get_field_display()
        items.append(_item(
            'lead', f'change-{c.pk}', c.created_at, f'{label} changed',
            by=_name(c.changed_by), before={label: c.from_value}, after={label: c.to_value},
        ))

    for e in mine(lead.course_interests.select_related('course', 'created_by', 'source')):
        items.append(_item(
            'enquiry', e.pk, e.created_at, 'Enquiry added',
            detail=e.source.name if e.source else '', by=_name(e.created_by),
            course=e.label or None, enquiry=e.pk,
        ))
    changes = mine(EnquiryStageChange.objects.filter(enquiry__lead=lead).select_related(
        'enquiry__course', 'changed_by',
    ), 'enquiry__branch_id')
    for c in changes:
        items.append(_item(
            'enquiry', f'stage-{c.pk}', c.created_at,
            f'Enquiry {c.get_to_stage_display().lower()}',
            detail=c.note, by=_name(c.changed_by),
            course=c.enquiry.label or None, enquiry=c.enquiry_id,
        ))

    for c in mine(LeadCall.objects.filter(lead=lead).select_related('created_by')):
        items.append(_item(
            'call', c.pk, c.call_timestamp, f'{c.get_direction_display()} call',
            detail=_join(c.outcome, c.notes), by=_name(c.created_by) or c.caller,
            duration_seconds=c.duration_seconds,
        ))

    for a in mine(LeadActivity.objects.filter(lead=lead).select_related('performed_by')):
        items.append(_item(
            'note', a.pk, a.activity_at, a.title,
            detail=a.description, by=_name(a.performed_by),
        ))

    follow_ups = mine(FollowUp.objects.filter(lead=lead).select_related(
        'created_by', 'updated_by', 'assigned_to', 'enrollment__course',
    ))
    for f in follow_ups:
        course = _course(f.enrollment.course) if f.enrollment_id else None
        items.append(_item(
            'follow_up', f.pk, f.created_at, 'Follow-up set',
            detail=_join(f.purpose, _name(f.assigned_to)), by=_name(f.created_by),
            course=course, enrollment=f.enrollment_id, after={'due': f.scheduled_at.isoformat()},
        ))
        if f.completed_at:
            items.append(_item(
                'follow_up', f'done-{f.pk}', f.completed_at, 'Follow-up done',
                detail=f.notes, by=_name(f.updated_by or f.assigned_to),
                course=course, enrollment=f.enrollment_id,
            ))

    notes = mine(VoiceNote.objects.filter(lead=lead).select_related('created_by', 'enquiry__course'))
    for v in notes:
        items.append(_item(
            'voice_note', v.pk, v.created_at, 'Voice note',
            detail=v.note, by=_name(v.created_by),
            course=(v.enquiry.label or None) if v.enquiry_id else None,
            enquiry=v.enquiry_id, voice_note=v.pk, duration_seconds=v.duration_seconds,
        ))

    for en in mine(lead.enrollments.select_related('course', 'created_by')):
        items.append(_item(
            'enrollment', en.pk, en.created_at, 'Enrollment added',
            by=_name(en.created_by), course=_course(en.course), enrollment=en.pk,
            amount=str(en.agreed_amount),
        ))
    history = mine(EnrollmentHistory.objects.filter(enrollment__lead=lead).select_related(
        'enrollment__course', 'performed_by',
    ), 'enrollment__branch_id')
    for h in history:
        items.append(_item(
            HISTORY_KIND.get(h.action, 'money'), h.pk, h.created_at, h.get_action_display(),
            detail=h.reason, by=_name(h.performed_by), course=_course(h.enrollment.course),
            enrollment=h.enrollment_id, before=h.before, after=h.after,
        ))

    payments = mine(Payment.objects.filter(enrollment__lead=lead).select_related(
        'enrollment__course', 'received_by',
    ))
    for p in payments:
        items.append(_item(
            'money', f'payment-{p.pk}', p.payment_date, 'Payment received',
            detail=_join(p.get_payment_method_display(), p.transaction_reference),
            by=_name(p.received_by), course=_course(p.enrollment.course),
            enrollment=p.enrollment_id, amount=str(p.amount),
        ))

    invoices = mine(Invoice.objects.filter(enrollment__lead=lead).select_related('created_by'))
    for i in invoices:
        items.append(_item(
            'money', f'invoice-{i.pk}', i.created_at,
            f'{INVOICE_TITLE.get(i.type, "Invoice")} {i.invoice_number}',
            detail=f'Emailed to {i.emailed_to}' if i.sent_at and i.emailed_to else '',
            by=_name(i.created_by), course=i.course_name, enrollment=i.enrollment_id,
            amount=str(i.amount),
        ))

    reminders = mine(
        ReminderLog.objects.filter(enrollment__lead=lead).select_related('enrollment__course'),
    )
    for r in reminders:
        items.append(_item(
            'reminder', r.pk, r.created_at, f'Reminder: {r.get_step_display().lower()}',
            detail=_join(r.get_channel_display(), r.get_status_display()),
            course=_course(r.enrollment.course), enrollment=r.enrollment_id, amount=str(r.amount),
        ))

    for d in mine(LeadDocument.objects.filter(lead=lead).select_related('uploaded_by')):
        items.append(_item(
            'document', d.pk, d.uploaded_at, d.document_type, by=_name(d.uploaded_by),
        ))

    items.sort(key=lambda i: i['at'], reverse=True)
    return items
