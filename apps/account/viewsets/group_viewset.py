from django.contrib.auth.models import Group
from rest_framework import viewsets
from rest_framework.permissions import SAFE_METHODS, IsAdminUser, IsAuthenticated
from apps.account.serializers.group_serializer import GroupSerializer
from apps.global_config.pagination import CustomPagination


class GroupViewSet(viewsets.ModelViewSet):
    queryset = Group.objects.prefetch_related('permissions').order_by('name')
    serializer_class = GroupSerializer
    pagination_class = CustomPagination

    def get_permissions(self):
        # Anyone signed in may read roles (the counselor list and the user
        # form need them); only admins change them.
        if self.request.method in SAFE_METHODS:
            return [IsAuthenticated()]
        return [IsAdminUser()]
