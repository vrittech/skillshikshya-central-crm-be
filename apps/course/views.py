from django.db.models import Count, Q
from rest_framework import viewsets
from rest_framework.filters import SearchFilter, OrderingFilter
from rest_framework.permissions import IsAuthenticated

from django_filters.rest_framework import DjangoFilterBackend

from apps.course.models import Course, Batch, CourseBatch, CourseCategory
from apps.course.filters import CourseFilter, BatchFilter, CourseBatchFilter, CourseCategoryFilter
from apps.course.serializers import (
    CourseSerializer, BatchSerializer, CourseBatchSerializer, CourseCategorySerializer,
)
from apps.global_config.pagination import CustomPagination
from apps.global_config.permissions import IsStaffOrHasPermission
from apps.global_config.filters import BranchFilterBackend


class CourseCategoryViewSet(viewsets.ModelViewSet):
    queryset = CourseCategory.objects.all()
    serializer_class = CourseCategorySerializer
    pagination_class = CustomPagination
    filterset_class = CourseCategoryFilter
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter, BranchFilterBackend]
    search_fields = ['name', 'description']
    ordering_fields = ['id', 'name']
    ordering = ['name']

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            permission_classes = [IsAuthenticated, IsStaffOrHasPermission]
        else:
            permission_classes = [IsAuthenticated]
        return [p() for p in permission_classes]


class CourseViewSet(viewsets.ModelViewSet):
    queryset = Course.objects.all()
    serializer_class = CourseSerializer
    pagination_class = CustomPagination
    filterset_class = CourseFilter
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter, BranchFilterBackend]
    search_fields = ['name', 'remarks']
    ordering_fields = ['id', 'name', 'full_amount', 'course_type']
    ordering = ['name']

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            permission_classes = [IsAuthenticated, IsStaffOrHasPermission]
        else:
            permission_classes = [IsAuthenticated]
        return [p() for p in permission_classes]


class BatchViewSet(viewsets.ModelViewSet):
    queryset = Batch.objects.all()
    serializer_class = BatchSerializer
    pagination_class = CustomPagination
    filterset_class = BatchFilter
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter, BranchFilterBackend]
    search_fields = ['name', 'description']
    ordering_fields = ['id', 'name']
    ordering = ['name']

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            permission_classes = [IsAuthenticated, IsStaffOrHasPermission]
        else:
            permission_classes = [IsAuthenticated]
        return [p() for p in permission_classes]


class CourseBatchViewSet(viewsets.ModelViewSet):
    queryset = CourseBatch.objects.all()
    serializer_class = CourseBatchSerializer

    def get_queryset(self):
        from apps.lead.models import Enrollment

        return CourseBatch.objects.select_related('course', 'batch', 'created_by').annotate(
            seats_taken=Count(
                'seated_enrollments',
                filter=Q(seated_enrollments__status__in=Enrollment.SEAT_STATUSES),
                distinct=True,
            ),
            waiting_count=Count(
                'enrollments',
                filter=Q(enrollments__status=Enrollment.Status.PENDING),
                distinct=True,
            ),
        )
    pagination_class = CustomPagination
    filterset_class = CourseBatchFilter
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter, BranchFilterBackend]
    search_fields = ['name', 'coursebatch_id']
    ordering_fields = ['id', 'name', 'coursebatch_id', 'start_date', 'end_date']
    ordering = ['-created_at']

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            permission_classes = [IsAuthenticated, IsStaffOrHasPermission]
        else:
            permission_classes = [IsAuthenticated]
        return [p() for p in permission_classes]
