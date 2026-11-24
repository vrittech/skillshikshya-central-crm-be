from django.contrib import admin

from apps.course.models import Batch, BatchCost, Course, CourseBatch, CourseCategory
from apps.lead.admin import ReadOnlyAdmin


class CourseAdmin(admin.ModelAdmin):
    list_display = ['name', 'course_type', 'full_amount', 'one_time_payment_discount']
    list_filter = ['course_type']
    search_fields = ['name']


class BatchAdmin(admin.ModelAdmin):
    list_display = ['name', 'is_active']
    list_filter = ['is_active']
    search_fields = ['name']


class CourseBatchAdmin(admin.ModelAdmin):
    list_display = [
        'coursebatch_id', 'name', 'course', 'batch', 'status', 'mode', 'capacity',
        'start_date', 'end_date', 'is_active',
    ]
    list_filter = ['status', 'mode', 'is_active', 'course', 'batch']
    search_fields = ['coursebatch_id', 'name']


class CourseCategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'is_active']
    list_filter = ['is_active']
    search_fields = ['name']


class BatchCostAdmin(ReadOnlyAdmin):
    list_display = ['course_batch', 'kind', 'amount', 'spent_on', 'created_by', 'voided_at']
    list_filter = ['kind']
    search_fields = ['course_batch__coursebatch_id', 'course_batch__name', 'note']


admin.site.register(Course, CourseAdmin)
admin.site.register(Batch, BatchAdmin)
admin.site.register(CourseBatch, CourseBatchAdmin)
admin.site.register(CourseCategory, CourseCategoryAdmin)
admin.site.register(BatchCost, BatchCostAdmin)
