from rest_framework import viewsets
from rest_framework.filters import SearchFilter, OrderingFilter
from rest_framework.permissions import IsAuthenticated

from django_filters.rest_framework import DjangoFilterBackend

from apps.branch.models import Branch
from apps.branch.filters import BranchFilter
from apps.branch.serializers import BranchSerializer
from apps.global_config.pagination import CustomPagination
from apps.global_config.permissions import IsStaffOrHasPermission, OPEN, ReadRights


class BranchViewSet(ReadRights, viewsets.ModelViewSet):
    read_perm = OPEN
    queryset = Branch.objects.all()
    serializer_class = BranchSerializer
    pagination_class = CustomPagination
    filterset_class = BranchFilter
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    search_fields = ['name', 'code', 'address']
    ordering_fields = ['id', 'name', 'code', 'is_active']
    ordering = ['name']

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            permission_classes = [IsAuthenticated, IsStaffOrHasPermission]
        else:
            permission_classes = [IsAuthenticated]
        return [p() for p in permission_classes]
