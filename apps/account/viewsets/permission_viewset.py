from django.contrib.auth.models import Permission
from rest_framework import viewsets
from rest_framework.permissions import IsAdminUser
from rest_framework import mixins
from apps.account.serializers.permission_serializer import PermissionSerializer
from apps.global_config.pagination import CustomPagination


class PermissionViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    # queryset = Permission.objects.select_related('content_type').all()  # all
    # queryset = Permission.objects.filter(content_type__app_label__in=['account', 'auth'])  # app wise 
    queryset = Permission.objects.filter(content_type__model__in=[
        'user', 'group', 'permission', 'otp',
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
    ]
    ).exclude(  # exclude by
        content_type__app_label='auth',
        codename__in=['add_permission', 'change_permission', 'delete_permission'],
    )#.exclude(
    #     codename__startswith='delete_',
    #     content_type__model__in=['user', 'group', 'branch'],
    # )
    serializer_class = PermissionSerializer
    permission_classes = [IsAdminUser]
    pagination_class = CustomPagination
