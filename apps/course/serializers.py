from decimal import Decimal

from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers

from apps.course.models import Batch, BatchClosing, BatchCost, Course, CourseBatch, CourseCategory


class CourseSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source='created_by.name', read_only=True)
    category_names = serializers.SerializerMethodField()

    class Meta:
        model = Course
        fields = '__all__'
        read_only_fields = ['created_at', 'updated_at', 'created_by']

    def get_category_names(self, obj):
        return [c.name for c in obj.categories.all()]


class CourseCategorySerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source='created_by.name', read_only=True)

    class Meta:
        model = CourseCategory
        fields = '__all__'
        read_only_fields = ['created_at', 'updated_at', 'created_by']


class BatchSerializer(serializers.ModelSerializer):
    """A shift. Start and end are times of day; the end is after the start."""

    MIN_MINUTES = 15

    created_by_name = serializers.CharField(source='created_by.name', read_only=True)
    start_time = serializers.TimeField(format='%H:%M')
    end_time = serializers.TimeField(format='%H:%M')
    duration_minutes = serializers.IntegerField(read_only=True)

    class Meta:
        model = Batch
        fields = '__all__'
        read_only_fields = ['created_at', 'updated_at', 'created_by']

    def validate(self, attrs):
        instance = self.instance
        start = attrs.get('start_time', getattr(instance, 'start_time', None))
        end = attrs.get('end_time', getattr(instance, 'end_time', None))
        if start is not None and end is not None:
            if end <= start:
                raise serializers.ValidationError({'end_time': 'The end time must be after the start time.'})
            minutes = (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)
            if minutes < self.MIN_MINUTES:
                raise serializers.ValidationError(
                    {'end_time': f'A shift must be at least {self.MIN_MINUTES} minutes.'}
                )

        name = (attrs.get('name') or getattr(instance, 'name', '') or '').strip()
        if 'name' in attrs:
            attrs['name'] = name
            if not name:
                raise serializers.ValidationError({'name': 'Enter a name.'})
        request = self.context.get('request')
        if 'branch' in attrs:
            branch = attrs['branch']
        elif instance is not None:
            branch = instance.branch
        else:
            branch = getattr(getattr(request, 'user', None), 'branch', None)
        same = Batch.objects.filter(name__iexact=name, branch=branch)
        if instance is not None:
            same = same.exclude(pk=instance.pk)
        if name and same.exists():
            raise serializers.ValidationError({'name': f'A shift named {name} already exists.'})
        return attrs


class CourseBatchSerializer(serializers.ModelSerializer):
    course_name = serializers.CharField(source='course.name', read_only=True)
    batch_name = serializers.CharField(source='batch.name', read_only=True)
    branch_name = serializers.CharField(source='branch.name', read_only=True, default=None)
    created_by_name = serializers.CharField(source='created_by.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    mode_display = serializers.CharField(source='get_mode_display', read_only=True)
    batch_start_time = serializers.TimeField(
        source='batch.start_time', read_only=True, default=None, format='%H:%M',
    )
    batch_end_time = serializers.TimeField(
        source='batch.end_time', read_only=True, default=None, format='%H:%M',
    )
    capacity = serializers.IntegerField(
        min_value=1,
        error_messages={
            'required': 'Enter the number of seats.',
            'null': 'Enter the number of seats.',
            'invalid': 'Enter the number of seats.',
            'min_value': 'A batch needs at least 1 seat.',
        },
    )
    seats_taken = serializers.SerializerMethodField()
    seats_left = serializers.SerializerMethodField()
    waiting_count = serializers.SerializerMethodField()

    class Meta:
        model = CourseBatch
        fields = '__all__'
        read_only_fields = ['coursebatch_id', 'created_at', 'updated_at', 'created_by']

    # The list view annotates these counts in one query; a single batch
    # (create, update) counts them here.
    def get_seats_taken(self, obj):
        taken = getattr(obj, 'seats_taken', None)
        return obj.count_seats_taken() if taken is None else taken

    def get_seats_left(self, obj):
        if obj.capacity is None:
            return None
        return max(obj.capacity - self.get_seats_taken(obj), 0)

    def get_waiting_count(self, obj):
        """Pending enrollments that chose this batch: they take a seat on confirm."""
        waiting = getattr(obj, 'waiting_count', None)
        if waiting is None:
            waiting = obj.enrollments.filter(status='pending').count()
        return waiting

    def validate_capacity(self, value):
        if self.instance is not None:
            taken = self.instance.count_seats_taken()
            if value < taken:
                raise serializers.ValidationError(
                    f'{taken} students sit in this batch now, so it needs at least {taken} seats.'
                )
        return value

    def validate_status(self, value):
        closed = CourseBatch.Status.CLOSED
        was_closed = self.instance is not None and self.instance.status == closed
        if value == closed and not was_closed:
            raise serializers.ValidationError('Use Close batch, with a reason.')
        if was_closed and value != closed:
            raise serializers.ValidationError('Use Reopen batch, with a reason.')
        return value

    def validate_course(self, value):
        batch = self.instance
        if batch is not None and value != batch.course and (
            batch.enrollments.exists() or batch.placements.exists()
        ):
            raise serializers.ValidationError(
                'Students are enrolled in this batch, so its course cannot change.'
            )
        return value

    def validate_branch(self, value):
        from apps.lead.models import Enrollment

        batch = self.instance
        if batch is not None and value != batch.branch and Enrollment.objects.filter(
            Q(batch=batch) | Q(current_batch=batch),
        ).exclude(status=Enrollment.Status.CANCELLED).exists():
            raise serializers.ValidationError(
                'Students are enrolled in this batch, so its branch cannot change.'
            )
        return value


class BatchCostSerializer(serializers.ModelSerializer):
    kind_display = serializers.CharField(source='get_kind_display', read_only=True)
    created_by_name = serializers.CharField(source='created_by.name', read_only=True)
    voided_by_name = serializers.CharField(source='voided_by.name', read_only=True, default=None)

    class Meta:
        model = BatchCost
        fields = [
            'id', 'course_batch', 'kind', 'kind_display', 'amount', 'spent_on', 'note',
            'created_by', 'created_by_name', 'created_at',
            'voided_at', 'voided_by', 'voided_by_name', 'void_reason',
        ]
        read_only_fields = fields


class AddCostSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=BatchCost.Kind.choices)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.01'))
    spent_on = serializers.DateField(required=False)
    note = serializers.CharField(required=False, allow_blank=True, default='')

    def validate(self, attrs):
        attrs.setdefault('spent_on', timezone.localdate())
        return attrs


class CloseSerializer(serializers.Serializer):
    reason = serializers.CharField()
    close_anyway = serializers.BooleanField(required=False, default=False)


class RegisterMarkSerializer(serializers.Serializer):
    student = serializers.IntegerField(min_value=1)
    status = serializers.ChoiceField(choices=['present', 'absent'])


class RegisterSerializer(serializers.Serializer):
    date = serializers.DateField()
    marks = RegisterMarkSerializer(many=True, allow_empty=False)


class BatchClosingSerializer(serializers.ModelSerializer):
    kind_display = serializers.CharField(source='get_kind_display', read_only=True)
    created_by_name = serializers.CharField(source='created_by.name', read_only=True)

    class Meta:
        model = BatchClosing
        fields = [
            'id', 'kind', 'kind_display', 'reason',
            'students', 'fees', 'collected', 'owed', 'costs', 'profit', 'checklist',
            'created_by', 'created_by_name', 'created_at',
        ]
        read_only_fields = fields
