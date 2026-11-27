import django_filters
from apps.course.models import Course, Batch, CourseBatch, CourseCategory


class CourseCategoryFilter(django_filters.FilterSet):
    name = django_filters.CharFilter(lookup_expr='icontains')

    class Meta:
        model = CourseCategory
        fields = ['name', 'is_active']


class CourseFilter(django_filters.FilterSet):
    name = django_filters.CharFilter(lookup_expr='icontains')
    course_type = django_filters.ChoiceFilter(choices=Course.CourseType.choices)

    class Meta:
        model = Course
        fields = ['name', 'course_type']


class BatchFilter(django_filters.FilterSet):
    name = django_filters.CharFilter(lookup_expr='icontains')

    class Meta:
        model = Batch
        fields = ['name', 'is_active']


class CourseBatchFilter(django_filters.FilterSet):
    name = django_filters.CharFilter(lookup_expr='icontains')
    coursebatch_id = django_filters.CharFilter(lookup_expr='icontains')
    start_date = django_filters.DateFilter()
    end_date = django_filters.DateFilter()
    status = django_filters.MultipleChoiceFilter(choices=CourseBatch.Status.choices)
    mode = django_filters.ChoiceFilter(choices=CourseBatch.Mode.choices)

    class Meta:
        model = CourseBatch
        fields = [
            'name', 'coursebatch_id', 'course', 'batch', 'start_date', 'end_date',
            'is_active', 'status', 'mode',
        ]
