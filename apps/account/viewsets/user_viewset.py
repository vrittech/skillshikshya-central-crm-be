import csv

from django.http import HttpResponse

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter, OrderingFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from django_filters.rest_framework import DjangoFilterBackend

from apps.account.models import User
from apps.account.filters import UserFilter
from apps.account.serializers.user_serializer import UserMiniSerializer, UserSerializer
from apps.global_config.pagination import CustomPagination
from apps.global_config.permissions import IsStaffOrHasPermission, OPEN, ReadRights
from apps.global_config.filters import BranchFilterBackend


class UserViewSet(ReadRights, viewsets.ModelViewSet):
    read_perm = OPEN
    read_perms = {
        'export_csv': 'account.view_user',
    }
    queryset = User.objects.all()
    serializer_class = UserSerializer
    pagination_class = CustomPagination
    http_method_names = ['get', 'put', 'patch', 'delete', 'head', 'options']
    filterset_class = UserFilter
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter, BranchFilterBackend]
    branch_field = 'branch'
    search_fields = ['name', 'email', 'designation', 'phone']
    ordering_fields = ['id', 'name', 'email', 'is_active', 'is_staff']
    ordering = ['id']

    def get_queryset(self):
        user = self.request.user
        if user.is_staff or user.has_perm('account.view_user'):
            return User.objects.all()
        return User.objects.filter(id=user.id)

    def get_permissions(self):
        if self.action in ['update', 'partial_update', 'destroy']:
            permission_classes = [IsAuthenticated, IsStaffOrHasPermission]
        else:
            permission_classes = [IsAuthenticated]
        return [p() for p in permission_classes]

    @action(detail=False, methods=['get'])
    def me(self, request):
        """The signed-in user with their current permissions, including those from groups."""
        return Response(UserMiniSerializer(request.user).data)

    @action(detail=False, methods=['get'])
    def export_csv(self, request):
        users = self.filter_queryset(self.get_queryset())
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="users.csv"'

        writer = csv.writer(response)
        writer.writerow([
            'ID', 'Name', 'Email', 'Designation', 'Phone',
            'Is Active', 'Is Staff', 'Is Superuser',
            'Daily Call Target (min)', 'Monthly Enrollment Target',
            'Shift Start', 'Shift End',
            'Work Sun', 'Work Mon', 'Work Tue', 'Work Wed',
            'Work Thu', 'Work Fri', 'Work Sat',
            'Exclude From Calls', 'Exclude From Enrollment',
        ])

        for user in users:
            writer.writerow([
                user.id,
                user.name,
                user.email,
                user.designation or '',
                user.phone or '',
                'Yes' if user.is_active else 'No',
                'Yes' if user.is_staff else 'No',
                'Yes' if user.is_superuser else 'No',
                user.daily_call_target_min or '',
                user.monthly_enrollment_target or '',
                user.shift_start.strftime('%H:%M') if user.shift_start else '',
                user.shift_end.strftime('%H:%M') if user.shift_end else '',
                'Yes' if user.work_sun else 'No',
                'Yes' if user.work_mon else 'No',
                'Yes' if user.work_tue else 'No',
                'Yes' if user.work_wed else 'No',
                'Yes' if user.work_thu else 'No',
                'Yes' if user.work_fri else 'No',
                'Yes' if user.work_sat else 'No',
                'Yes' if user.exclude_from_calls else 'No',
                'Yes' if user.exclude_from_enrollment else 'No',
            ])

        return response
