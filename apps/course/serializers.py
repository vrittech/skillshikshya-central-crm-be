from rest_framework import serializers
from apps.course.models import Course, Batch, CourseBatch, CourseCategory


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
    created_by_name = serializers.CharField(source='created_by.name', read_only=True)

    class Meta:
        model = Batch
        fields = '__all__'
        read_only_fields = ['created_at', 'updated_at', 'created_by']


class CourseBatchSerializer(serializers.ModelSerializer):
    course_name = serializers.CharField(source='course.name', read_only=True)
    batch_name = serializers.CharField(source='batch.name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    mode_display = serializers.CharField(source='get_mode_display', read_only=True)
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
        if self.instance is not None and value is not None:
            taken = self.instance.count_seats_taken()
            if value < taken:
                raise serializers.ValidationError(
                    f'{taken} students sit in this batch now, so it needs at least {taken} seats.'
                )
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
