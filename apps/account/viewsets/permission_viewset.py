from django.contrib.auth.models import Permission
from rest_framework import viewsets
from rest_framework.permissions import IsAdminUser
from rest_framework import mixins
from apps.account.serializers.permission_serializer import PermissionSerializer
from apps.global_config.pagination import CustomPagination
from apps.global_config.permissions import ReadRights, STAFF


class PermissionViewSet(ReadRights, mixins.ListModelMixin, viewsets.GenericViewSet):
    read_perm = STAFF
    # The rights the role screen offers. Roles themselves are admin-only, so
    # rights on roles and permissions would do nothing and are left out.
    queryset = Permission.objects.select_related('content_type').filter(content_type__model__in=[
        'user',
        'branch',
        'course',
        'leadsource', 'campaign', 'leadstatus', 'leadpriority',
        'leadtag', 'learningmode', 'institution',
        'province', 'city', 'lead', 'leadcourseinterest',
        'enrollment', 'payment', 'leadcall', 'leadactivity',
        'followup', 'leaddocument', 'notification',
        'student', 'studentdocument', 'attendance',
        'coursebatch', 'batch', 'batchcost', 'batchclosing',
        'installment', 'paymentplan', 'adjustment', 'invoice',
        'voicenote', 'teamslicence', 'teamsaccount',
    ]).exclude(content_type__app_label='auth')
    serializer_class = PermissionSerializer
    permission_classes = [IsAdminUser]
    pagination_class = CustomPagination
