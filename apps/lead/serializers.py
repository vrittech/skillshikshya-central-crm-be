from decimal import Decimal

from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from django.core.exceptions import ValidationError as DjangoValidationError

from apps.account.models import User
from apps.callminutes.utils import format_duration, parse_duration_to_seconds
from apps.branch.models import Branch
from apps.course.models import Course, CourseBatch
from apps.global_config.filters import walled_branch_id
from apps.lead.services import STATUS_CHOICES
from apps.lead.models import (
    LearningMode, Institution, Province, City, LeadSource, Campaign, LeadStatus, LeadPriority,
    LeadTag, Lead, LeadCourseInterest, Enrollment, Payment, EnrollmentHistory,
    Adjustment, Placement, ReminderLog, TeamsAccount, TeamsLicence, EnquiryStageChange,
    LeadCall, LeadActivity, FollowUp, LeadDocument, Notification, VoiceNote,
)


def _walled_branch_id(field):
    """The branch the requesting user is kept inside (None for staff). Takes a serializer or field."""
    request = field.context.get('request')
    return walled_branch_id(request.user) if request else None


def can_pick_course(field, course):
    """A branch user picks their branch's courses and the shared ones; staff pick any."""
    branch_id = _walled_branch_id(field)
    return branch_id is None or course.branch_id in (None, branch_id)


def check_assignee(user, branch, field):
    """A lead or follow-up goes to an active user of its branch, as in the import.

    A user with no branch (an admin) can take one of any branch.
    """
    if user is None:
        return
    if not user.is_active:
        raise ValidationError({field: [f'{user.name} is not an active user.']})
    if branch is not None and user.branch_id and user.branch_id != branch.pk:
        raise ValidationError({field: [f'{user.name} is not in {branch.name}.']})


class PrimaryCourseField(serializers.Field):
    """Nested {id, name, price} on read; accepts an int pk or {id} on write."""

    def to_representation(self, value):
        if value is None:
            return None
        return {
            'id': value.id,
            'name': value.name,
            'price': str(value.full_amount) if value.full_amount is not None else None,
        }

    def to_internal_value(self, data):
        course_id = data.get('id') if isinstance(data, dict) else data
        try:
            course = Course.objects.get(pk=course_id)
        except (Course.DoesNotExist, TypeError, ValueError):
            raise ValidationError(f'Invalid pk "{course_id}" - course does not exist.')
        # A course of another branch is hidden from a branch user, unless the lead has it already.
        lead = getattr(self.parent, 'instance', None)
        if course.pk != getattr(lead, 'primary_course_id', None) and not can_pick_course(self, course):
            raise ValidationError(f'Invalid pk "{course_id}" - course does not exist.')
        return course


class LeadSourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeadSource
        fields = '__all__'


class CampaignSerializer(serializers.ModelSerializer):
    class Meta:
        model = Campaign
        fields = '__all__'


class LeadStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeadStatus
        fields = '__all__'


class LeadPrioritySerializer(serializers.ModelSerializer):
    class Meta:
        model = LeadPriority
        fields = '__all__'


class LeadTagSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeadTag
        fields = '__all__'


class LearningModeSerializer(serializers.ModelSerializer):
    class Meta:
        model = LearningMode
        fields = '__all__'


class InstitutionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Institution
        fields = '__all__'


class ProvinceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Province
        fields = '__all__'


class CitySerializer(serializers.ModelSerializer):
    province_name = serializers.CharField(source='province.name', read_only=True)

    class Meta:
        model = City
        fields = '__all__'


class LeadCourseInterestSerializer(serializers.ModelSerializer):
    """An enquiry. The stage changes only through the stage and lose actions."""

    course_name = serializers.SerializerMethodField()
    stage_display = serializers.CharField(source='get_stage_display', read_only=True)
    counselor_name = serializers.CharField(source='counselor.name', read_only=True, default=None)
    source_name = serializers.CharField(source='source.name', read_only=True, default=None)
    lost_reason_display = serializers.CharField(
        source='get_lost_reason_display', read_only=True, default=None,
    )
    lost_at_stage_display = serializers.CharField(
        source='get_lost_at_stage_display', read_only=True, default=None,
    )
    branch_name = serializers.CharField(source='branch.name', read_only=True, default=None)
    created_by_name = serializers.CharField(source='created_by.name', read_only=True, default=None)

    class Meta:
        model = LeadCourseInterest
        fields = '__all__'
        read_only_fields = [
            'lead', 'created_at', 'updated_at', 'course_name', 'stage', 'stage_changed_at',
            'lost_reason', 'lost_note', 'lost_at_stage', 'enrollment', 'created_by', 'branch',
        ]

    def get_course_name(self, obj):
        return obj.course.name if obj.course else obj.course_name

    def validate_course(self, value):
        enquiry = self.instance
        if value is not None and value != getattr(enquiry, 'course', None) and not can_pick_course(self, value):
            raise serializers.ValidationError(f'Invalid pk "{value.pk}" - object does not exist.')
        if enquiry is not None and value != enquiry.course and enquiry.stage != 'new':
            raise serializers.ValidationError(
                'The course of an enquiry that has moved past New cannot change. '
                'Mark it lost and add a new enquiry.'
            )
        return value


class EnquiryStageChangeSerializer(serializers.ModelSerializer):
    from_stage_display = serializers.CharField(source='get_from_stage_display', read_only=True)
    to_stage_display = serializers.CharField(source='get_to_stage_display', read_only=True)
    changed_by_name = serializers.CharField(source='changed_by.name', read_only=True, default=None)

    class Meta:
        model = EnquiryStageChange
        fields = [
            'id', 'from_stage', 'from_stage_display', 'to_stage', 'to_stage_display',
            'note', 'changed_by', 'changed_by_name', 'created_at',
        ]
        read_only_fields = fields


class EnquiryStageSerializer(serializers.Serializer):
    stage = serializers.ChoiceField(choices=LeadCourseInterest.Stage.choices)
    note = serializers.CharField(required=False, allow_blank=True, default='')


class EnquiryLostSerializer(serializers.Serializer):
    reason = serializers.ChoiceField(choices=LeadCourseInterest.LostReason.choices)
    note = serializers.CharField(required=False, allow_blank=True, default='')


class PlacementSerializer(serializers.ModelSerializer):
    """One row of an enrollment's seat history."""

    kind_display = serializers.CharField(source='get_kind_display', read_only=True)
    course_batch_name = serializers.CharField(source='course_batch.name', read_only=True)
    from_batch_name = serializers.CharField(source='from_batch.name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.name', read_only=True)

    class Meta:
        model = Placement
        fields = [
            'id', 'kind', 'kind_display', 'course_batch', 'course_batch_name',
            'from_batch', 'from_batch_name', 'started_on', 'reason',
            'created_by', 'created_by_name', 'created_at',
        ]
        read_only_fields = fields


class SeatActionSerializer(serializers.Serializer):
    """Body of Join batch, Move, Pause and Resume.

    Join, Move and Resume need a batch. Move, Pause and Resume need a reason.
    started_on is the day the change takes effect (today if left out).
    """

    batch = serializers.PrimaryKeyRelatedField(queryset=CourseBatch.objects.all())
    reason = serializers.CharField()
    started_on = serializers.DateField(required=False)

    def __init__(self, *args, needs_batch=True, needs_reason=True, **kwargs):
        super().__init__(*args, **kwargs)
        if not needs_batch:
            self.fields.pop('batch')
        if not needs_reason:
            self.fields['reason'] = serializers.CharField(required=False, allow_blank=True)


class DuePartSerializer(serializers.Serializer):
    due_on = serializers.DateField()
    amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal('0.01'),
    )


class FirstPaymentSerializer(serializers.Serializer):
    """The payment taken with Confirm, when nothing is paid yet."""

    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    payment_method = serializers.ChoiceField(choices=Payment.Method.choices)
    payment_date = serializers.DateTimeField(required=False)
    transaction_reference = serializers.CharField(required=False, allow_blank=True, max_length=255)
    notes = serializers.CharField(required=False, allow_blank=True)

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError('Amount must be more than 0.')
        return value


class DueDatesSerializer(serializers.Serializer):
    """Body of Change due dates (reason needed) and of Confirm (all optional)."""

    due_dates = DuePartSerializer(many=True, allow_empty=False)
    reason = serializers.CharField()

    def __init__(self, *args, for_confirm=False, **kwargs):
        super().__init__(*args, **kwargs)
        if for_confirm:
            self.fields.pop('reason')
            self.fields['due_dates'] = DuePartSerializer(many=True, required=False)
            self.fields['first_payment'] = FirstPaymentSerializer(required=False)


class DueRowSerializer(serializers.Serializer):
    """One row of services.due_rows()."""

    kind = serializers.CharField()
    due_on = serializers.DateField(allow_null=True)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    paid = serializers.DecimalField(max_digits=12, decimal_places=2)
    left = serializers.DecimalField(max_digits=12, decimal_places=2)
    status = serializers.CharField()
    days_late = serializers.IntegerField()


class EnrollmentSerializer(serializers.ModelSerializer):
    course_name = serializers.CharField(source='course.name', read_only=True)
    lead_name = serializers.CharField(source='lead.full_name', read_only=True)
    student_name = serializers.CharField(source='student.full_name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.name', read_only=True)
    batch_name = serializers.CharField(source='batch.name', read_only=True)
    current_batch_name = serializers.CharField(source='current_batch.name', read_only=True)
    seat_state = serializers.SerializerMethodField()
    placements = PlacementSerializer(many=True, read_only=True)
    due_dates = serializers.SerializerMethodField()
    has_fee_invoice = serializers.SerializerMethodField()
    teams_licence = serializers.SerializerMethodField()
    total_paid = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    extra_charges_total = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True,
    )
    discounts_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    write_offs_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    refunds_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    remaining_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    is_full_paid = serializers.BooleanField(read_only=True)

    PRICE_FIELDS = ('listed_fee', 'standard_discount', 'additional_discount', 'agreed_amount')

    class Meta:
        model = Enrollment
        fields = '__all__'
        read_only_fields = [
            # The student is linked by Confirm enrollment, never by hand, and
            # the seat changes only through the seat actions.
            'lead', 'student', 'current_batch',
            'created_by', 'updated_by', 'created_at', 'updated_at',
            'total_paid', 'remaining_amount', 'is_full_paid',
            # The listed fee is the price set on the course. It follows the course.
            'listed_fee',
        ]

    def _price_is_open(self):
        """The price can change while the enrollment is pending and nothing is paid."""
        enrollment = self.instance
        return (
            enrollment.status == Enrollment.Status.PENDING
            and not enrollment.payments.filter(status=Payment.Status.SUCCESSFUL).exists()
        )

    def get_due_dates(self, obj):
        """Due dates with what is paid on each. Empty while the enrollment is pending."""
        from apps.lead.services import due_rows

        if obj.status not in Enrollment.SEAT_STATUSES:
            return []
        return DueRowSerializer(due_rows(obj), many=True).data

    def get_teams_licence(self, obj):
        """The Teams licence the student holds now, or None. Uses the prefetch."""
        licence = next((t for t in obj.teams_licences.all() if t.is_open), None)
        if licence is None:
            return None
        from apps.lead.teams import STATE_LABELS, licence_state

        state, reason = licence_state(licence)
        return {
            'id': licence.id, 'teams_account': licence.teams_account_id, 'account': licence.account,
            'assigned_at': licence.assigned_at, 'paused_at': licence.paused_at,
            'details_sent_at': licence.details_sent_at, 'details_sent_to': licence.details_sent_to,
            'state': state, 'state_label': STATE_LABELS[state], 'reason': reason,
        }

    def get_has_fee_invoice(self, obj):
        """True when the enrollment has an active course-fee invoice."""
        return any(
            i.type == 'enrollment' and i.status == 'active' for i in obj.invoices.all()
        )

    def get_seat_state(self, obj):
        """'seated', 'paused' or 'none'. Uses the prefetched seat history."""
        if obj.current_batch_id:
            return 'seated'
        rows = list(obj.placements.all())  # newest first
        return 'paused' if rows and rows[0].kind == Placement.Kind.PAUSE else 'none'

    def _changed(self, attrs, field):
        return field in attrs and attrs[field] != getattr(self.instance, field)

    def _listed_fee_from_course(self, attrs):
        """A new enrollment, or a new course, takes the price set on the course."""
        if self.instance is not None and not self._changed(attrs, 'course'):
            return
        course = attrs.get('course')
        if course is None:
            return
        if self.instance is not None and not self._price_is_open():
            raise serializers.ValidationError({'course': (
                'Something is paid on this enrollment, so its course and price are fixed. '
                'Cancel it with a reason and add a new one.'
            )})
        if course.full_amount is None:
            raise serializers.ValidationError(
                {'course': 'This course has no price. Set its full amount on the course first.'}
            )
        attrs['listed_fee'] = course.full_amount

    def _check_price(self, attrs):
        """Discounts and the agreed amount stay within the listed fee.

        Catches a typo (100000 for 10000) before an invoice is made. Anything
        above the listed fee is an extra charge, added later with a reason.
        """
        if self.instance is not None and not any(f in attrs for f in (*self.PRICE_FIELDS, 'course')):
            return
        from apps.lead.emails import npr

        def value(field):
            if field in attrs:
                return Decimal(attrs[field] if attrs[field] is not None else 0)
            return Decimal(getattr(self.instance, field, 0) or 0)

        listed = value('listed_fee')
        standard, additional = value('standard_discount'), value('additional_discount')
        agreed = value('agreed_amount')
        errors = {}
        for field, amount in (('standard_discount', standard), ('additional_discount', additional)):
            if amount < 0:
                errors[field] = "A discount can't be below 0."
        if not errors and standard + additional > listed:
            errors['additional_discount' if additional else 'standard_discount'] = (
                f'The discounts add up to more than the listed fee ({npr(listed)}).'
            )
        top = listed - standard - additional
        if agreed < 0:
            errors['agreed_amount'] = "The agreed amount can't be below 0."
        elif not errors and agreed > top:
            errors['agreed_amount'] = (
                f"The agreed amount can't be more than {npr(top)} "
                f'(the listed fee{" less discounts" if top != listed else ""}). '
                'Add an extra charge for anything more.'
            )
        if errors:
            raise serializers.ValidationError(errors)

    def validate(self, attrs):
        # Once confirmed or paid, the price is fixed. Later changes are money
        # entries with a reason.
        if self.instance is not None and not self._price_is_open():
            errors = {
                field: 'The price is fixed once the enrollment is confirmed or paid. '
                       'Add an extra charge or a discount with a reason instead.'
                for field in self.PRICE_FIELDS
                if self._changed(attrs, field)
            }
            if errors:
                raise serializers.ValidationError(errors)

        # Once confirmed, the course and the money batch are fixed too.
        if self.instance is not None and self.instance.status != Enrollment.Status.PENDING:
            errors = {}
            if self._changed(attrs, 'course'):
                errors['course'] = (
                    'The course is fixed once the enrollment is confirmed. To switch '
                    'course, cancel this enrollment with a reason and add a new one.'
                )
            if self._changed(attrs, 'batch'):
                errors['batch'] = (
                    'The batch is fixed once the enrollment is confirmed, because its '
                    'money counts there. Use Move to change where the student sits.'
                )
            if errors:
                raise serializers.ValidationError(errors)

        self._listed_fee_from_course(attrs)
        self._check_price(attrs)

        if 'batch' in attrs or 'course' in attrs:
            course = attrs.get('course', getattr(self.instance, 'course', None))
            batch = attrs.get('batch', getattr(self.instance, 'batch', None))
            if batch is not None and course is not None and batch.course_id != course.id:
                raise serializers.ValidationError(
                    {'batch': 'This batch is for another course. Pick a batch of this course.'}
                )
            # The seat count decides: a closed, full or seatless batch can't be
            # picked. The seat itself is taken on confirm, checked again there.
            batch_is_new = self.instance is None or self._changed(attrs, 'batch')
            refusal = batch.seat_refusal() if batch is not None and batch_is_new else None
            if refusal:
                raise serializers.ValidationError({'batch': refusal})
        return attrs

    def validate_status(self, value):
        # A status changes only through its action (each needs a reason and
        # writes the history), never through a plain edit.
        current = self.instance.status if self.instance else None
        pending = Enrollment.Status.PENDING
        cancelled = Enrollment.Status.CANCELLED
        if self.instance is None:
            if value != pending:
                raise serializers.ValidationError(
                    'A new enrollment starts as pending. Confirm it to make the lead a student.'
                )
            return value
        if value == current:
            return value
        if value == cancelled:
            raise serializers.ValidationError('To cancel, use Cancel enrollment and give a reason.')
        if current == cancelled:
            raise serializers.ValidationError('To reopen, use Reinstate enrollment and give a reason.')
        if current == pending:
            raise serializers.ValidationError('To confirm, use Confirm enrollment.')
        raise serializers.ValidationError('To change the status, use Change status and give a reason.')


class StatusChangeSerializer(serializers.Serializer):
    """Body of Change status."""

    status = serializers.ChoiceField(choices=[(value.value, value.label) for value in STATUS_CHOICES])
    reason = serializers.CharField()


def active_receipt_number(payment):
    """Number of the payment's active receipt, or None. Reads the prefetched invoices."""
    for invoice in payment.invoices.all():
        if invoice.status == 'active':
            return invoice.invoice_number
    return None


class PaymentSerializer(serializers.ModelSerializer):
    received_by_name = serializers.CharField(source='received_by.name', read_only=True)
    voided_by_name = serializers.CharField(source='voided_by.name', read_only=True)
    receipt_number = serializers.SerializerMethodField()

    def get_receipt_number(self, payment):
        return active_receipt_number(payment)

    class Meta:
        model = Payment
        fields = '__all__'
        read_only_fields = [
            'enrollment', 'received_by', 'created_at',
            'void_reason', 'voided_by', 'voided_at',
        ]

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError('Amount must be more than 0.')
        return value

    def validate_status(self, value):
        # A new payment is pending or successful. Confirm and Void change it later.
        if value not in (Payment.Status.PENDING, Payment.Status.SUCCESSFUL):
            raise serializers.ValidationError(
                'A new payment must be pending or successful.'
            )
        return value


class EnrollmentHistorySerializer(serializers.ModelSerializer):
    action_display = serializers.CharField(source='get_action_display', read_only=True)
    performed_by_name = serializers.CharField(source='performed_by.name', read_only=True)

    class Meta:
        model = EnrollmentHistory
        fields = [
            'id', 'enrollment', 'action', 'action_display', 'reason',
            'before', 'after', 'performed_by', 'performed_by_name', 'created_at',
        ]
        read_only_fields = fields


class AdjustmentSerializer(serializers.ModelSerializer):
    kind_display = serializers.CharField(source='get_kind_display', read_only=True)
    created_by_name = serializers.CharField(source='created_by.name', read_only=True)
    amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal('0.01'),
    )
    reason = serializers.CharField()

    class Meta:
        model = Adjustment
        fields = [
            'id', 'enrollment', 'kind', 'kind_display', 'amount', 'reason',
            'branch', 'created_by', 'created_by_name', 'created_at',
        ]
        read_only_fields = ['id', 'enrollment', 'branch', 'created_by', 'created_at']


class ReminderLogSerializer(serializers.ModelSerializer):
    step_display = serializers.CharField(source='get_step_display', read_only=True)
    channel_display = serializers.CharField(source='get_channel_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = ReminderLog
        fields = [
            'id', 'due_on', 'amount', 'step', 'step_display', 'channel', 'channel_display',
            'sent_to', 'status', 'status_display', 'error', 'followup', 'created_at',
        ]
        read_only_fields = fields


class ReasonSerializer(serializers.Serializer):
    """Body of every action that needs a reason. Blank or spaces-only is refused."""

    reason = serializers.CharField()


class LeadMinSerializer(serializers.ModelSerializer):
    class Meta:
        model = Lead
        fields = ['id', 'reference_number', 'full_name', 'phone', 'status', 'priority', 'lead_score']


class LeadListSerializer(serializers.ModelSerializer):
    status_name = serializers.CharField(source='status.name', read_only=True)
    priority_name = serializers.CharField(source='priority.name', read_only=True)
    priority_color = serializers.CharField(source='priority.color', read_only=True)
    status_color = serializers.CharField(source='status.color', read_only=True)
    source_name = serializers.CharField(source='source.name', read_only=True)
    campaign_name = serializers.CharField(source='campaign.name', read_only=True)
    assigned_counselor_name = serializers.CharField(source='assigned_counselor.name', read_only=True)
    branch_name = serializers.CharField(source='branch.name', read_only=True)
    primary_course = PrimaryCourseField(required=False, allow_null=True)
    province_name = serializers.CharField(source='province.name', read_only=True)
    city_name = serializers.CharField(source='city.name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.name', read_only=True)
    updated_by_name = serializers.CharField(source='updated_by.name', read_only=True)

    class Meta:
        model = Lead
        fields = '__all__'


class LeadImportSerializer(serializers.Serializer):
    """An import file, what to do with duplicates, and the defaults for blanks."""

    file = serializers.FileField()
    duplicates = serializers.ChoiceField(choices=['skip', 'import'], default='skip')
    default_source = serializers.PrimaryKeyRelatedField(
        queryset=LeadSource.objects.all(), required=False, allow_null=True)
    default_campaign = serializers.PrimaryKeyRelatedField(
        queryset=Campaign.objects.all(), required=False, allow_null=True)
    default_status = serializers.PrimaryKeyRelatedField(
        queryset=LeadStatus.objects.all(), required=False, allow_null=True)
    default_priority = serializers.PrimaryKeyRelatedField(
        queryset=LeadPriority.objects.all(), required=False, allow_null=True)
    default_course = serializers.PrimaryKeyRelatedField(
        queryset=Course.objects.all(), required=False, allow_null=True)
    default_branch = serializers.PrimaryKeyRelatedField(
        queryset=Branch.objects.all(), required=False, allow_null=True)
    default_counselor = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(is_active=True), required=False, allow_null=True)

    def validate(self, attrs):
        user = self.context['request'].user
        branch_id = walled_branch_id(user)
        branch = attrs.get('default_branch')
        if branch_id and branch is not None and branch.pk != branch_id:
            raise ValidationError({'default_branch': ['You can add leads only to your own branch.']})
        counselor = attrs.get('default_counselor')
        branch_for_counselor = branch.pk if branch is not None else branch_id
        if counselor is not None and counselor.branch_id and branch_for_counselor \
                and counselor.branch_id != branch_for_counselor:
            raise ValidationError({'default_counselor': ['This counselor is not in that branch.']})
        return attrs

    def defaults(self):
        d = self.validated_data
        keys = ('source', 'campaign', 'status', 'priority', 'course', 'branch', 'counselor')
        return {k: d[f'default_{k}'] for k in keys if d.get(f'default_{k}') is not None}


class LeadSerializer(serializers.ModelSerializer):
    status_name = serializers.CharField(source='status.name', read_only=True)
    priority_name = serializers.CharField(source='priority.name', read_only=True)
    priority_color = serializers.CharField(source='priority.color', read_only=True)
    status_color = serializers.CharField(source='status.color', read_only=True)
    source_name = serializers.CharField(source='source.name', read_only=True)
    campaign_name = serializers.CharField(source='campaign.name', read_only=True)
    assigned_counselor_name = serializers.CharField(source='assigned_counselor.name', read_only=True)
    branch_name = serializers.CharField(source='branch.name', read_only=True)
    primary_course = PrimaryCourseField(required=False, allow_null=True)
    province_name = serializers.CharField(source='province.name', read_only=True)
    city_name = serializers.CharField(source='city.name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.name', read_only=True)
    updated_by_name = serializers.CharField(source='updated_by.name', read_only=True)

    call_count = serializers.SerializerMethodField()
    next_followup = serializers.SerializerMethodField()
    enrollment_summary = serializers.SerializerMethodField()

    class Meta:
        model = Lead
        fields = '__all__'
        read_only_fields = [
            'reference_number', 'created_at', 'updated_at', 'created_by', 'updated_by',
            'is_converted', 'converted_at', 'is_deleted', 'deleted_at', 'deleted_by',
            'lead_score',
        ]

    def validate_expected_revenue(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError("Expected revenue can't be below 0.")
        return value

    def validate(self, attrs):
        # The branch, as in the import: a branch user adds leads to their own
        # branch only, and only staff move a lead to another branch.
        lead = self.instance
        branch_id = _walled_branch_id(self)
        if branch_id is not None:
            if lead is None:
                if attrs.get('branch') is not None and attrs['branch'].pk != branch_id:
                    raise ValidationError({'branch': ['You can add leads only to your own branch.']})
                attrs['branch'] = self.context['request'].user.branch
            elif 'branch' in attrs and attrs['branch'] != lead.branch:
                raise ValidationError({'branch': ['Only staff can move a lead to another branch.']})

        # A new counselor, or a new branch, is checked; an unchanged one is kept.
        branch = attrs['branch'] if 'branch' in attrs else getattr(lead, 'branch', None)
        counselor = attrs.get('assigned_counselor', getattr(lead, 'assigned_counselor', None))
        if lead is None or counselor != lead.assigned_counselor or branch != lead.branch:
            check_assignee(counselor, branch, 'assigned_counselor')
        return attrs

    def update(self, instance, validated_data):
        # The form sends only its own custom data: keep the file an import came from.
        imported = (instance.custom_data or {}).get('import_file')
        if imported and 'custom_data' in validated_data:
            validated_data['custom_data'] = {
                **(validated_data['custom_data'] or {}), 'import_file': imported,
            }
        return super().update(instance, validated_data)

    # A branch user sees only their branch's calls, follow-ups and enrollments, as in every list.

    def get_call_count(self, obj):
        calls = obj.calls.all()
        branch_id = _walled_branch_id(self)
        if branch_id:
            calls = calls.filter(branch_id=branch_id)
        return calls.count()

    def get_next_followup(self, obj):
        followups = obj.followups.filter(status='pending')
        branch_id = _walled_branch_id(self)
        if branch_id:
            followups = followups.filter(branch_id=branch_id)
        followup = followups.order_by('scheduled_at').first()
        if followup:
            return {
                'id': followup.id,
                'scheduled_at': followup.scheduled_at,
                'purpose': followup.purpose,
                'assigned_to': followup.assigned_to_id,
                'assigned_to_name': followup.assigned_to.name if followup.assigned_to else None,
            }
        return None

    def get_enrollment_summary(self, obj):
        enrollments = obj.enrollments.all()
        branch_id = _walled_branch_id(self)
        if branch_id:
            enrollments = enrollments.filter(branch_id=branch_id)
        enrollment = enrollments.order_by('-enrollment_date').first()
        if enrollment:
            return {
                'is_enrolled': True,
                'latest_status': enrollment.status,
                'course': enrollment.course.name,
                'batch': enrollment.batch.coursebatch_id if enrollment.batch else None,
            }
        return {'is_enrolled': False, 'latest_status': None, 'course': None, 'batch': None}


class BranchSetOnCreate:
    """The branch is set when the record is added (views.new_record_branch) and never changes."""

    def update(self, instance, validated_data):
        validated_data.pop('branch', None)
        return super().update(instance, validated_data)


class LeadCallSerializer(BranchSetOnCreate, serializers.ModelSerializer):
    caller_name = serializers.CharField(source='caller', read_only=True)
    created_by_name = serializers.CharField(source='created_by.name', read_only=True)
    duration_input = serializers.CharField(write_only=True, required=False)
    duration_formatted = serializers.SerializerMethodField()

    class Meta:
        model = LeadCall
        fields = '__all__'
        read_only_fields = ['lead', 'created_by', 'created_at']

    def get_duration_formatted(self, obj):
        return format_duration(obj.duration_seconds)

    def validate_duration_seconds(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError("The duration can't be below 0.")
        return value

    def to_internal_value(self, data):
        if 'duration_input' in data and data.get('duration_input'):
            try:
                data['duration_seconds'] = parse_duration_to_seconds(
                    data['duration_input'],
                )
            except DjangoValidationError as exc:
                raise serializers.ValidationError({
                    'duration_input': exc.messages[0] if exc.messages else str(exc),
                })
        return super().to_internal_value(data)

    def create(self, validated_data):
        validated_data.pop('duration_input', None)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        # duration_seconds and caller are locked after creation so the
        # call-minutes ledger cannot be gamed.
        validated_data.pop('duration_seconds', None)
        validated_data.pop('duration_input', None)
        validated_data.pop('caller', None)
        return super().update(instance, validated_data)


class GlobalCallSerializer(serializers.ModelSerializer):
    lead_name = serializers.CharField(source='lead.full_name', read_only=True)
    lead_reference_number = serializers.CharField(
        source='lead.reference_number', read_only=True,
    )
    created_by_name = serializers.CharField(source='created_by.name', read_only=True)

    class Meta:
        model = LeadCall
        fields = [
            'id', 'lead', 'lead_name', 'lead_reference_number',
            'call_timestamp', 'direction', 'caller', 'phone_number',
            'duration_seconds', 'outcome', 'notes', 'recording_url',
            'next_followup_at', 'next_action',
            'branch', 'created_by', 'created_by_name', 'created_at',
        ]
        read_only_fields = fields


class LeadActivitySerializer(BranchSetOnCreate, serializers.ModelSerializer):
    performed_by_name = serializers.CharField(source='performed_by.name', read_only=True)

    class Meta:
        model = LeadActivity
        fields = '__all__'
        # performed_by is who added it, set when it is added.
        read_only_fields = ['lead', 'performed_by', 'created_at']


class FollowUpSerializer(BranchSetOnCreate, serializers.ModelSerializer):
    assigned_to_name = serializers.CharField(source='assigned_to.name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.name', read_only=True)
    updated_by_name = serializers.CharField(source='updated_by.name', read_only=True)
    enrollment_course_name = serializers.CharField(
        source='enrollment.course.name', read_only=True, default=None,
    )

    class Meta:
        model = FollowUp
        fields = '__all__'
        # enrollment is set only by the payment reminder job.
        read_only_fields = [
            'lead', 'enrollment', 'created_by', 'updated_by', 'created_at', 'updated_at',
            'reminder_sent',
        ]


class LeadDocumentSerializer(BranchSetOnCreate, serializers.ModelSerializer):
    uploaded_by_name = serializers.CharField(source='uploaded_by.name', read_only=True)

    class Meta:
        model = LeadDocument
        fields = '__all__'
        read_only_fields = ['lead', 'uploaded_by', 'uploaded_at']


class VoiceNoteSerializer(serializers.ModelSerializer):
    """Upload: multipart with `file` (audio), optional `enquiry`, `duration_seconds`, `note`."""

    file = serializers.FileField(write_only=True)
    course_name = serializers.SerializerMethodField()
    created_by_name = serializers.CharField(source='created_by.name', read_only=True, default=None)

    class Meta:
        model = VoiceNote
        fields = [
            'id', 'lead', 'enquiry', 'course_name', 'content_type', 'size_bytes',
            'duration_seconds', 'note', 'branch', 'created_by', 'created_by_name', 'created_at',
            'file',
        ]
        read_only_fields = ['lead', 'content_type', 'size_bytes', 'branch', 'created_by', 'created_at']

    def get_course_name(self, obj):
        enquiry = obj.enquiry
        if enquiry is None:
            return None
        return enquiry.label or None

    def validate_file(self, file):
        from apps.lead import voice_notes

        if voice_notes.base_type(file.content_type) not in voice_notes.AUDIO_TYPES:
            raise ValidationError('Only a sound recording can be saved.')
        if file.size > voice_notes.MAX_BYTES:
            raise ValidationError('The recording is too large (10 MB at most).')
        if file.size == 0:
            raise ValidationError('The recording is empty.')
        return file

    def validate_duration_seconds(self, value):
        from apps.lead.voice_notes import MAX_SECONDS

        if value > MAX_SECONDS:
            raise ValidationError('A voice note can be 10 minutes at most.')
        return value

    def validate_enquiry(self, enquiry):
        lead_pk = self.context.get('lead_pk')
        if enquiry is not None and lead_pk is not None and enquiry.lead_id != int(lead_pk):
            raise ValidationError('This enquiry belongs to another lead.')
        return enquiry


class GlobalFollowUpSerializer(serializers.ModelSerializer):
    lead_name = serializers.CharField(source='lead.full_name', read_only=True)
    lead_phone = serializers.CharField(source='lead.phone', read_only=True)
    assigned_to_name = serializers.CharField(source='assigned_to.name', read_only=True)
    branch_name = serializers.CharField(source='branch.name', read_only=True)
    enrollment_course_name = serializers.CharField(
        source='enrollment.course.name', read_only=True, default=None,
    )
    due = serializers.SerializerMethodField()

    class Meta:
        model = FollowUp
        fields = [
            'id', 'lead', 'lead_name', 'lead_phone',
            'assigned_to', 'assigned_to_name',
            'scheduled_at', 'due', 'purpose', 'status',
            'enrollment', 'enrollment_course_name',
            'branch', 'branch_name',
        ]
        read_only_fields = fields

    def get_due(self, obj):
        """'overdue', 'today' or 'upcoming', by Nepal days."""
        from apps.lead.filters import due_bucket, nepal_today_bounds

        if not hasattr(self, '_today'):
            self._today = nepal_today_bounds()
        return due_bucket(obj.scheduled_at, self._today)


class GlobalEnrollmentSerializer(serializers.ModelSerializer):
    lead_name = serializers.CharField(source='lead.full_name', read_only=True)
    student_name = serializers.CharField(source='student.full_name', read_only=True)
    course_name = serializers.CharField(source='course.name', read_only=True)
    branch_name = serializers.CharField(source='branch.name', read_only=True)
    batch_name = serializers.CharField(source='batch.name', read_only=True)
    current_batch_name = serializers.CharField(source='current_batch.name', read_only=True)
    total_paid = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    remaining_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    is_full_paid = serializers.BooleanField(read_only=True)
    seat_state = serializers.SerializerMethodField()
    paused_on = serializers.SerializerMethodField()
    paused_from_name = serializers.SerializerMethodField()

    class Meta:
        model = Enrollment
        fields = [
            'id', 'lead', 'lead_name',
            'student', 'student_name',
            'course', 'course_name',
            'batch', 'batch_name', 'current_batch', 'current_batch_name',
            'seat_state', 'paused_on', 'paused_from_name',
            'status', 'enrollment_date',
            'listed_fee', 'agreed_amount',
            'total_paid', 'remaining_amount', 'is_full_paid',
            'branch', 'branch_name',
            'created_at',
        ]
        read_only_fields = fields


    # These read the seat annotations from services.with_seat().
    def get_seat_state(self, obj):
        from apps.lead.services import annotated_seat_state

        return annotated_seat_state(obj)

    def get_paused_on(self, obj):
        paused = self.get_seat_state(obj) == 'paused' and obj.seat_on
        return obj.seat_on.isoformat() if paused else None

    def get_paused_from_name(self, obj):
        return obj.seat_from_name if self.get_seat_state(obj) == 'paused' else None


class GlobalPaymentSerializer(serializers.ModelSerializer):
    lead = serializers.IntegerField(source='enrollment.lead_id', read_only=True)
    lead_name = serializers.CharField(source='enrollment.lead.full_name', read_only=True)
    course_name = serializers.CharField(source='enrollment.course.name', read_only=True)
    received_by_name = serializers.CharField(source='received_by.name', read_only=True)
    branch_name = serializers.CharField(source='branch.name', read_only=True)
    receipt_number = serializers.SerializerMethodField()

    def get_receipt_number(self, payment):
        return active_receipt_number(payment)

    class Meta:
        model = Payment
        fields = [
            'id', 'enrollment',
            'lead', 'lead_name',
            'course_name',
            'amount', 'payment_date',
            'payment_method', 'transaction_reference',
            'status', 'void_reason', 'voided_at',
            'received_by', 'received_by_name',
            'branch', 'branch_name',
            'receipt_number',
        ]
        read_only_fields = fields


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = '__all__'
        read_only_fields = ['recipient', 'created_at']


class TeamsLicenceSerializer(serializers.ModelSerializer):
    lead = serializers.IntegerField(source='enrollment.lead_id', read_only=True)
    name = serializers.SerializerMethodField()
    student_id = serializers.CharField(source='enrollment.student.student_id', read_only=True, default=None)
    course_name = serializers.CharField(source='enrollment.course.name', read_only=True)
    batch_name = serializers.CharField(
        source='enrollment.current_batch.name', read_only=True, default=None,
    )
    assigned_by_name = serializers.CharField(source='assigned_by.name', read_only=True)
    released_by_name = serializers.CharField(source='released_by.name', read_only=True, default=None)
    state = serializers.SerializerMethodField()

    class Meta:
        model = TeamsLicence
        fields = [
            'id', 'enrollment', 'lead', 'name', 'student_id', 'course_name', 'batch_name',
            'teams_account', 'account', 'assigned_by', 'assigned_by_name', 'assigned_at',
            'released_at', 'released_by', 'released_by_name', 'release_reason', 'paused_at',
            'details_sent_at', 'details_sent_to', 'state',
        ]
        read_only_fields = fields

    def get_name(self, obj):
        e = obj.enrollment
        return e.student.full_name if e.student_id else e.lead.full_name

    def get_state(self, obj):
        from apps.lead.teams import STATE_LABELS, licence_state

        if not obj.is_open:
            return None
        state, reason = licence_state(obj)
        return {'state': state, 'label': STATE_LABELS[state], 'reason': reason}


class GiveLicenceSerializer(serializers.Serializer):
    enrollment = serializers.PrimaryKeyRelatedField(queryset=Enrollment.objects.all())
    teams_account = serializers.PrimaryKeyRelatedField(queryset=TeamsAccount.objects.all())
    # Sent to the student with the account when send_details is on. Never kept.
    password = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False, max_length=256)
    send_details = serializers.BooleanField(default=False)

    def validate(self, attrs):
        if attrs['send_details'] and not attrs.get('password', '').strip():
            raise serializers.ValidationError({'password': 'Enter the password to send.'})
        return attrs


class SendTeamsDetailsSerializer(serializers.Serializer):
    password = serializers.CharField(trim_whitespace=False, max_length=256)

    def validate_password(self, value):
        if not value.strip():
            raise serializers.ValidationError('Enter the password to send.')
        return value


class AddTeamsAccountsSerializer(serializers.Serializer):
    emails = serializers.ListField(child=serializers.EmailField(), allow_empty=False, max_length=500)
    # Empty = any branch may give it.
    branch = serializers.PrimaryKeyRelatedField(
        queryset=Branch.objects.all(), required=False, allow_null=True,
    )


class PhoneMatchEnquirySerializer(serializers.ModelSerializer):
    course_name = serializers.SerializerMethodField()
    stage_display = serializers.CharField(source='get_stage_display', read_only=True)
    branch_name = serializers.CharField(source='branch.name', read_only=True, default=None)

    class Meta:
        model = LeadCourseInterest
        fields = ['id', 'course', 'course_name', 'stage', 'stage_display', 'branch_name']

    def get_course_name(self, obj):
        return obj.label or None


class PhoneMatchSerializer(serializers.ModelSerializer):
    """A lead found by phone number, from any branch."""

    branch_name = serializers.CharField(source='branch.name', read_only=True, default=None)
    counselor_name = serializers.CharField(source='assigned_counselor.name', read_only=True, default=None)
    status_name = serializers.CharField(source='status.name', read_only=True, default=None)
    enquiries = PhoneMatchEnquirySerializer(source='course_interests', many=True, read_only=True)

    class Meta:
        model = Lead
        fields = [
            'id', 'reference_number', 'full_name', 'phone', 'secondary_phone', 'email',
            'branch', 'branch_name', 'counselor_name', 'status_name', 'is_converted',
            'is_archived', 'created_at', 'enquiries',
        ]
        read_only_fields = fields

