from django.db.models import Count, F, Q
from django.shortcuts import get_object_or_404
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.filters import SearchFilter, OrderingFilter
from rest_framework.permissions import IsAuthenticated

from django_filters.rest_framework import DjangoFilterBackend

from apps.course import services
from apps.course.models import Course, Batch, BatchCost, CourseBatch, CourseCategory
from apps.course.filters import CourseFilter, BatchFilter, CourseBatchFilter, CourseCategoryFilter
from apps.course.money import batch_figures, figures_json
from apps.course.serializers import (
    AddCostSerializer, BatchClosingSerializer, BatchCostSerializer, BatchSerializer,
    CourseBatchSerializer, CourseCategorySerializer, CourseSerializer,
)
from apps.global_config.pagination import CustomPagination
from apps.global_config.permissions import IsStaffOrHasPermission, needs
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
    ordering_fields = ['id', 'name', 'start_time']
    # Morning first; a shift with no time set goes last.
    ordering = [F('start_time').asc(nulls_last=True), 'name']
    # A shift with no branch is shared by every branch.
    shared_when_no_branch = True

    def perform_create(self, serializer):
        branch = serializer.validated_data.get('branch') or getattr(self.request.user, 'branch', None)
        serializer.save(created_by=self.request.user, branch=branch)

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            permission_classes = [IsAuthenticated, IsStaffOrHasPermission]
        else:
            permission_classes = [IsAuthenticated]
        return [p() for p in permission_classes]


class CourseBatchViewSet(viewsets.ModelViewSet):
    queryset = CourseBatch.objects.all()
    serializer_class = CourseBatchSerializer
    pagination_class = CustomPagination
    filterset_class = CourseBatchFilter
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter, BranchFilterBackend]
    search_fields = ['name', 'coursebatch_id']
    ordering_fields = ['id', 'name', 'coursebatch_id', 'start_date', 'end_date']
    ordering = ['-created_at']

    def get_queryset(self):
        """Batches with their seat counts, counted in the same query."""
        from apps.lead.models import Enrollment

        return CourseBatch.objects.select_related('course', 'batch', 'branch', 'created_by').annotate(
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

    def perform_create(self, serializer):
        # A batch without a branch is hidden from branch users, so it takes
        # the creator's branch unless one is picked.
        branch = serializer.validated_data.get('branch') or getattr(self.request.user, 'branch', None)
        serializer.save(created_by=self.request.user, branch=branch)

    # Batch actions that aren't plain create/update need their own permission.
    ACTION_PERMS = {
        'add_cost': 'course.add_batchcost',
        'void_cost': 'course.change_batchcost',
        'close': 'course.change_coursebatch',
        'reopen': 'course.change_coursebatch',
    }

    def get_permissions(self):
        if self.action in self.ACTION_PERMS:
            permission_classes = [IsAuthenticated, needs(self.ACTION_PERMS[self.action])]
        elif self.action in ['create', 'update', 'partial_update', 'destroy']:
            permission_classes = [IsAuthenticated, IsStaffOrHasPermission]
        else:
            permission_classes = [IsAuthenticated]
        return [p() for p in permission_classes]

    def _money_reply(self, batch):
        """Figures now and, for a closed batch, at close and after close."""
        now = batch_figures([batch.pk])[batch.pk]
        closing = services.last_close(batch)
        at_close = services.close_figures(closing) if closing else None
        costs = batch.costs.select_related('created_by', 'voided_by')
        closings = batch.closings.select_related('created_by')
        return Response({
            'batch': batch.pk,
            'status': batch.status,
            'figures': figures_json(now),
            'at_close': figures_json(at_close) if at_close else None,
            'after_close': figures_json(services.after_close(now, at_close)) if at_close else None,
            'closed_at': closing.created_at if closing else None,
            'costs': BatchCostSerializer(costs, many=True).data,
            'closings': BatchClosingSerializer(closings, many=True).data,
        })

    def _reason(self, request):
        from apps.lead.serializers import ReasonSerializer

        body = ReasonSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        return body.validated_data['reason']

    @action(detail=True, methods=['post'])
    def close(self, request, *args, **kwargs):
        """Close the batch with a reason. Its figures are kept as they are today."""
        batch = services.close_batch(self.get_object(), reason=self._reason(request), user=request.user)
        return self._money_reply(batch)

    @action(detail=True, methods=['post'])
    def reopen(self, request, *args, **kwargs):
        batch = services.reopen_batch(self.get_object(), reason=self._reason(request), user=request.user)
        return self._money_reply(batch)

    @action(detail=True, methods=['get'])
    def money(self, request, *args, **kwargs):
        """Fees, collected, owed, costs and profit of the batch, with its costs."""
        return self._money_reply(self.get_object())

    @action(detail=True, methods=['post'], url_path='costs')
    def add_cost(self, request, *args, **kwargs):
        batch = self.get_object()
        body = AddCostSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        services.add_cost(batch, user=request.user, **body.validated_data)
        return Response(self._money_reply(batch).data, status=201)

    @action(detail=True, methods=['post'], url_path=r'costs/(?P<cost_pk>\d+)/void')
    def void_cost(self, request, cost_pk=None, *args, **kwargs):
        batch = self.get_object()
        cost = get_object_or_404(BatchCost, pk=cost_pk, course_batch=batch)
        services.void_cost(cost, reason=self._reason(request), user=request.user)
        return self._money_reply(batch)

    @action(detail=True, methods=['get'])
    def students(self, request, *args, **kwargs):
        """Who sits in this batch now, who moved in from another batch, and who is paused."""
        from apps.lead.services import batch_students

        return Response(batch_students(self.get_object()))
